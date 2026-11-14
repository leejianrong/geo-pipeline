"""The Detect tab: upload a GeoTIFF, pick a model, watch progress, review detections."""

import asyncio
import json
import os
from datetime import UTC, datetime

import httpx
from common import Client, data_uri, fetch, reach
from indah import (
    Button,
    Card,
    Col,
    Download,
    ImageOverlay,
    Progress,
    Row,
    Select,
    Signal,
    Slider,
    Spinner,
    Stat,
    Table,
    Text,
    TextInput,
    Upload,
    UploadedFile,
)

from geopipe.viewer import filter_features, overlay_boxes

MODELS = os.environ.get("UI_MODELS", "rfdetr,yolox,rfdetr-buildings,yolox-buildings").split(",")
MAX_UPLOAD_MB = float(os.environ.get("UI_MAX_UPLOAD_MB", "200"))
TERMINAL = ("done", "incomplete", "error")
COLUMNS = [Col("job_id", "job", kind="mono"), Col("state", kind="badge", tones={
    "done": "ok", "incomplete": "warn", "error": "err"}), Col("progress"),
    Col("boxes", kind="number"), Col("submitted", kind="time")]


def job_row(s: dict) -> dict:
    return {"job_id": s["job_id"], "state": s.get("state", "?"),
            "progress": f"{s.get('tiles_detected', 0)}/{s.get('total', '?')}",
            "boxes": int(s.get("boxes_final", 0)),
            "submitted": datetime.fromtimestamp(float(s.get("submitted_at", 0)), UTC).isoformat()}


def detect_view(client: Client):
    """The tab's components and a coroutine that reloads its job list (called after sign-in)."""
    status = Signal("")
    model = Signal(next((m for m in MODELS if m != "stub"), MODELS[0]))  # stub is a test backend
    busy = Signal(False)
    progress = Signal(0.0)
    rows = Signal([])
    selected = Signal(None)
    preview = Signal("")
    size = Signal((1, 1))
    features = Signal([])
    min_score = Signal(0.3)
    class_text = Signal("")
    geojson = Signal(None)

    shown = lambda: filter_features(features.value, min_score.value, class_text.value)  # noqa: E731
    boxes = lambda: overlay_boxes(shown(), *size.value)  # noqa: E731

    async def refresh() -> None:
        resp = await client.request("GET", "/jobs")
        if resp.status_code == 200:
            rows.set([job_row(s) for s in resp.json()])

    async def open_job(job_id: str) -> None:
        resp = await client.request("GET", f"/jobs/{job_id}/result")
        if resp.status_code != 200:
            status.set(f"Job {job_id[:8]}: {resp.json().get('detail', resp.status_code)}")
            return
        body = resp.json()
        raw = await fetch(body["url"])
        geojson.set(raw)
        features.set(json.loads(raw)["features"])
        size.set((body["width"] or 1, body["height"] or 1))
        if body["preview_url"]:
            preview.set(data_uri(await fetch(body["preview_url"]), "image/jpeg"))
        status.set(f"Job {job_id[:8]} ({body['state']}): {len(features.value)} detections.")

    async def track(job_id: str) -> None:
        while True:
            s = (await client.request("GET", f"/jobs/{job_id}")).json()
            total = int(s.get("total", 0))
            progress.set(int(s.get("tiles_detected", 0)) / total if total else 0.0)
            status.set(f"Job {job_id[:8]}: {s['state']}")
            if s["state"] in TERMINAL:
                break
            await asyncio.sleep(1)
        await refresh()
        if s["state"] == "error":
            status.set(f"Job {job_id[:8]} failed: {s.get('error', 'unknown error')}")
        else:
            await open_job(job_id)

    async def on_upload(file: UploadedFile) -> None:
        if not client.signed_in.value or busy.value:
            status.set("Sign in first." if not client.signed_in.value
                       else "A job is already running.")
            return
        busy.set(True)
        try:
            up = (await client.request("POST", "/uploads")).json()
            target, headers = reach(up["upload_url"])
            async with httpx.AsyncClient(timeout=600) as c:
                put = await c.put(target, content=file.data, headers=headers)
            if put.status_code != 200:
                status.set(f"Upload failed ({put.status_code}).")
                return
            resp = await client.request("POST", "/jobs", json={"key": up["key"], "options": {
                "model": model.value}})
            if resp.status_code != 202:
                status.set(f"Could not start the job: {resp.json().get('detail')}")
                return
            await track(resp.json()["job_id"])
        finally:
            busy.set(False)

    async def on_open() -> None:
        if selected.value:
            await open_job(selected.value)

    cards = [
        Card(title="New job", children=[
            Select(model, options=MODELS, label="Model"),
            Upload(on_upload, accept=".tif,.tiff,image/tiff", label="GeoTIFF"),
            Spinner(active=busy, label="Working..."),
            Progress(progress, label="Tiles detected"),
            Text(lambda: status.value)]),
        Card(title="Jobs", children=[
            Table(rows=rows, columns=COLUMNS, key="job_id", selected=selected),
            Row(children=[Button("Refresh", on_click=refresh, variant="ghost"),
                          Button("Open selected", on_click=on_open)], gap="0.5rem")]),
        Card(title="Detections", children=[
            Slider(min_score, min=0, max=1, step=0.01, label="Minimum score"),
            TextInput(class_text, label="Class contains", placeholder="e.g. building"),
            Stat(lambda: len(shown()), label="Shown", help="detections passing the filters"),
            ImageOverlay(preview, boxes=boxes, label_mode="hover", alt="the raster"),
            Download(geojson, label="Download GeoJSON", filename="result.geojson",
                     media_type="application/geo+json")])]
    return cards, refresh
