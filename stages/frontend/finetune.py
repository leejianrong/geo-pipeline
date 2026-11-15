"""The Train tab's fine-tune section: pick a dataset and a preset, start a run, watch its epochs,
loss and validation AP, cancel it, and see past runs (ADR-0015, S4)."""

import asyncio

from common import Client, Segmented, Select, Table
from indah import Button, Card, Chart, Col, Number, Progress, Row, Signal, Steps, Text

POLL_SECONDS = 1.5
ACTIVE = ("queued", "waiting", "running")
PRESETS = ["fast", "balanced", "accurate", "best"]


def steps_of(run: dict) -> list[tuple]:
    """The run as three steps: queued, training, model registered."""
    state = run.get("state")
    if not state:
        return [("Queued", "pending"), ("Training", "pending"), ("Model registered", "pending")]
    epoch, epochs = run.get("epoch", 0), run.get("epochs", 0)
    waiting = "waiting for your other run to finish" if state == "waiting" else ""
    training = {"queued": "pending", "waiting": "pending", "running": "current",
                "done": "done"}.get(state, "skipped")
    detail = {"running": f"epoch {epoch} of {epochs}", "failed": run.get("error", ""),
              "cancelled": "cancelled", "done": f"{epochs} epochs"}.get(state, "")
    return [("Queued", "current" if state in ("queued", "waiting") else "done", waiting),
            ("Training", training, detail),
            ("Model registered", "done" if state == "done" else "pending",
             run.get("model_id") or "")]


def finetune_view(client: Client, on_model=None):
    """The section's components and a coroutine that loads datasets and past runs (after
    sign-in). `on_model` runs when a run ends in a model (the model view reloads its list)."""
    datasets = Signal([])
    dataset_id = Signal("")
    preset = Signal("fast")
    presets = Signal({})  # name -> {label, epochs}
    epochs = Signal(60.0)
    run = Signal({})  # the run being watched
    history = Signal([])
    runs = Signal([])
    message = Signal("Pick a dataset and a preset, then start.")

    def usable(d: dict) -> bool:
        return d["state"] == "ready" and "train" in d["splits"] and bool(
            {"valid", "test"} & set(d["splits"]))

    async def refresh() -> None:
        resp = await client.request("GET", "/datasets")
        if resp.status_code == 200:
            datasets.set([d for d in resp.json() if usable(d)])
            if datasets.value and dataset_id.value not in [d["id"] for d in datasets.value]:
                dataset_id.set(datasets.value[0]["id"])
        resp = await client.request("GET", "/train/presets")
        if resp.status_code == 200:
            first = not presets.value  # later refreshes must not undo the viewer's epochs
            presets.set({p["name"]: p for p in resp.json()})
            if first:
                pick_preset()
        resp = await client.request("GET", "/train/runs")
        if resp.status_code == 200:
            runs.set(resp.json())

    def pick_preset() -> None:
        epochs.set(float(presets.value.get(preset.value, {}).get("epochs", 60)))

    async def follow(run_id: str) -> None:
        while True:
            resp = await client.request("GET", f"/train/runs/{run_id}")
            if resp.status_code != 200:
                message.set("Lost track of the run.")
                return
            body = resp.json()
            run.set(body)
            history.set(body.get("history", []))
            if body["state"] not in ACTIVE:
                break
            await asyncio.sleep(POLL_SECONDS)
        await refresh()
        if body["state"] == "done":
            message.set(f"Done: model {body['model_id']} is registered.")
            if on_model:
                await on_model()
        elif body["state"] == "failed":
            message.set(f"The run failed: {body['error']}")
        else:
            message.set("The run was cancelled.")

    async def start() -> None:
        if not dataset_id.value:
            message.set("There is no dataset to train on: upload one above.")
            return
        resp = await client.request("POST", "/train/runs", json={
            "dataset_id": dataset_id.value, "preset": preset.value, "epochs": int(epochs.value)})
        if resp.status_code != 202:
            message.set(f"Could not start: {resp.json().get('detail')}")
            return
        message.set("Started.")
        run.set({"state": "queued", "epochs": int(epochs.value), "epoch": 0})
        history.set([])
        await follow(resp.json()["run_id"])

    async def cancel() -> None:
        if run.value.get("id") and run.value.get("state") in ACTIVE:
            await client.request("POST", f"/train/runs/{run.value['id']}/cancel")
            message.set("Cancelling...")

    def fraction() -> float:
        r = run.value
        return min(1.0, r.get("epoch", 0) / r["epochs"]) if r.get("epochs") else 0.0

    def status() -> str:
        r = run.value
        if not r.get("state"):
            return ""
        parts = [r["state"]]
        if r.get("epoch"):
            parts.append(f"epoch {r['epoch']}/{r['epochs']}")
        if r.get("loss") is not None:
            parts.append(f"loss {r['loss']:.3f}")
        if r.get("val_ap") is not None:
            parts.append(f"validation AP50 {r['val_ap']:.3f}")
        return ", ".join(parts)

    def curve(key: str):
        return lambda: [[h["epoch"], h[key]] for h in history.value if key in h]

    def past() -> list[dict]:
        return [{"id": r["id"], "dataset": r["dataset_id"], "preset": r["preset"],
                 "state": r["state"], "model": r.get("model_id") or ""} for r in runs.value]

    cards = [
        Card(title="Fine-tune", children=[
            Select(dataset_id, options=lambda: [d["id"] for d in datasets.value],
                   label="Training dataset"),
            Segmented(preset, options=PRESETS, label="Preset", on_change=pick_preset),
            Text(lambda: presets.value.get(preset.value, {}).get("label", "")),
            Number(epochs, min=1, max=200, step=1, label="Epochs"),
            Row(children=[
                Button("Start training", on_click=start,
                       disabled=lambda: run.value.get("state") in ACTIVE),
                Button("Cancel run", on_click=cancel, variant="ghost",
                       disabled=lambda: run.value.get("state") not in ACTIVE)], gap="0.5rem"),
            Text(lambda: message.value),
            Steps(lambda: steps_of(run.value), orientation="horizontal", label="Run progress"),
            Progress(fraction, label="Epochs done"),
            Text(status),
            Chart(curve("loss"), series=["loss"], x_label="epoch", height=160,
                  title="Training loss"),
            Chart(curve("val_ap"), series=["AP50"], x_label="epoch", height=160,
                  title="Validation AP at IoU 0.5"),
        ]),
        Card(title="Your runs", children=[
            Table(rows=past, key="id", height="14rem", columns=[
                Col("id"), Col("dataset"), Col("preset"), Col("state"), Col("model")]),
        ]),
    ]
    return cards, refresh
