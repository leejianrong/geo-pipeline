"""Web UI on indah: sign in with an API key, upload a GeoTIFF, watch progress, review detections.

The UI is a client of the HTTP API only (V6): it holds no Valkey or S3 credentials. File bytes
go browser -> this server -> presigned URL; this server is the only thing that talks to the
API, so the browser never sees the key's presigned URLs.
"""

import asyncio
import base64
import json
import os
from datetime import UTC, datetime

import httpx
from indah import (
    Button,
    Card,
    Col,
    Column,
    Download,
    ImageOverlay,
    Progress,
    Row,
    Select,
    Session,
    Signal,
    Slider,
    Spinner,
    Stat,
    Table,
    Text,
    TextInput,
    Upload,
    UploadedFile,
    create_app,
)

from geopipe.viewer import filter_features, overlay_boxes

API = os.environ.get("API_URL", "http://api:8000")
MODELS = os.environ.get("UI_MODELS", "rfdetr,yolox,rfdetr-buildings,yolox-buildings").split(",")
# Presigned URLs are signed for the address browsers use ("public"); inside compose this
# server reaches the store by another name. Set "public=internal"; unset when they are equal.
REWRITE = os.environ.get("S3_REWRITE", "")
MAX_UPLOAD_MB = float(os.environ.get("UI_MAX_UPLOAD_MB", "200"))
TERMINAL = ("done", "incomplete", "error")
COLUMNS = [Col("job_id", "job", kind="mono"), Col("state", kind="badge", tones={
    "done": "ok", "incomplete": "warn", "error": "err"}), Col("progress"),
    Col("boxes", kind="number"), Col("submitted", kind="time")]


def reach(url: str) -> tuple[str, dict]:
    if REWRITE and (pair := REWRITE.split("=", 1))[0] in url:
        return url.replace(pair[0], pair[1]), {"Host": pair[0]}
    return url, {}


async def fetch(url: str) -> bytes:
    target, headers = reach(url)
    async with httpx.AsyncClient(timeout=120) as c:
        resp = await c.get(target, headers=headers)
        resp.raise_for_status()
        return resp.content


def job_row(s: dict) -> dict:
    return {"job_id": s["job_id"], "state": s.get("state", "?"),
            "progress": f"{s.get('tiles_detected', 0)}/{s.get('total', '?')}",
            "boxes": int(s.get("boxes_final", 0)),
            "submitted": datetime.fromtimestamp(float(s.get("submitted_at", 0)), UTC).isoformat()}


def build_session() -> Session:
    key = Signal("")
    signed_in = Signal(False)
    message = Signal("Enter your API key to start.")
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

    async def api(method: str, path: str, **kw) -> httpx.Response:
        async with httpx.AsyncClient(timeout=60) as c:
            return await c.request(method, f"{API}{path}", headers={"X-API-Key": key.value}, **kw)

    async def refresh() -> None:
        resp = await api("GET", "/jobs")
        if resp.status_code == 200:
            rows.set([job_row(s) for s in resp.json()])

    async def sign_in() -> None:
        resp = await api("GET", "/jobs")
        signed_in.set(resp.status_code == 200)
        message.set("Signed in." if resp.status_code == 200 else "That API key was not accepted.")
        if resp.status_code == 200:
            rows.set([job_row(s) for s in resp.json()])

    async def open_job(job_id: str) -> None:
        resp = await api("GET", f"/jobs/{job_id}/result")
        if resp.status_code != 200:
            status.set(f"Job {job_id[:8]}: {resp.json().get('detail', resp.status_code)}")
            return
        body = resp.json()
        raw = await fetch(body["url"])
        geojson.set(raw)
        features.set(json.loads(raw)["features"])
        size.set((body["width"] or 1, body["height"] or 1))
        if body["preview_url"]:
            jpg = await fetch(body["preview_url"])
            preview.set("data:image/jpeg;base64," + base64.b64encode(jpg).decode())
        status.set(f"Job {job_id[:8]} ({body['state']}): {len(features.value)} detections.")

    async def track(job_id: str) -> None:
        while True:
            s = (await api("GET", f"/jobs/{job_id}")).json()
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
        if not signed_in.value or busy.value:
            status.set("Sign in first." if not signed_in.value else "A job is already running.")
            return
        busy.set(True)
        try:
            up = (await api("POST", "/uploads")).json()
            target, headers = reach(up["upload_url"])
            async with httpx.AsyncClient(timeout=600) as c:
                put = await c.put(target, content=file.data, headers=headers)
            if put.status_code != 200:
                status.set(f"Upload failed ({put.status_code}).")
                return
            resp = await api("POST", "/jobs", json={"key": up["key"], "options": {
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

    return Session(Column(children=[
        Text("# geo-pipeline\n\nUpload a GeoTIFF, pick a model, and review the detections.",
             markdown=True),
        Card(title="Sign in", children=[
            TextInput(key, label="API key", password=True, on_submit=sign_in),
            Button("Sign in", on_click=sign_in),
            Text(lambda: message.value)]),
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
                     media_type="application/geo+json")])]))


app = create_app(session_factory=build_session, max_upload_mb=MAX_UPLOAD_MB, title="geo-pipeline")
