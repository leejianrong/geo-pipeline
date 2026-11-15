"""The Train tab's Benchmark section: score your registered models on a dataset split in the
trainer and compare them with every other run of that split, built-ins included (ADR-0017, S6)."""

from common import Client, Segmented, Select, wait_job
from indah import Button, Card, Col, MultiSelect, Number, Signal, Table, Text

SPLITS = ["train", "valid", "test"]
COLUMNS = [Col("model"), Col("scorer"), Col("images", kind="number"), Col("map", "mAP"),
           Col("map50", "mAP50"), Col("precision", "P"), Col("recall", "R"), Col("f1", "F1"),
           Col("speed", "s/image")]


def run_row(run: dict, names: dict[str, str]) -> dict:
    m = run["metrics"]
    return {"id": run["id"], "model": names.get(run["model_id"], run["model_id"]),
            "scorer": run["scorer"], "images": int(m["tp"] + m["fn"]),
            **{k: f"{m[k]:.3f}" for k in ("map", "map50", "precision", "recall", "f1")},
            "speed": f"{m['median_latency']:.3f}"}


def benchmark_view(client: Client):
    """The section's components and a coroutine that loads models, datasets and runs."""
    models = Signal([])  # the viewer's registered ONNX models
    names = Signal({})  # model id -> name, built-ins too, for the table
    datasets = Signal([])
    chosen = Signal([])
    dataset_id = Signal("")
    split = Signal("test")
    limit = Signal(0.0)
    runs = Signal([])
    message = Signal("Pick models, a dataset and a split.")
    busy = Signal(False)

    def current() -> dict | None:
        return next((d for d in datasets.value if d["id"] == dataset_id.value), None)

    async def reload_runs() -> None:
        resp = await client.request("GET", f"/runs?dataset_id={dataset_id.value}")
        runs.set([r for r in resp.json() if r["split"] == split.value]
                 if resp.status_code == 200 else [])

    async def refresh() -> None:
        resp = await client.request("GET", "/models")
        if resp.status_code == 200:
            ready = [m for m in resp.json() if m["state"] == "ready"]
            names.set({m["id"]: m["name"] for m in ready})
            models.set([m for m in ready if m["scope"] == "own" and m["backend"] is None])
        resp = await client.request("GET", "/datasets")
        if resp.status_code == 200:
            datasets.set([d for d in resp.json() if d["state"] == "ready"])
            if datasets.value and not current():
                dataset_id.set(datasets.value[0]["id"])
        if current() and split.value not in current()["splits"]:
            split.set(next(s for s in SPLITS if s in current()["splits"]))
        await reload_runs()

    async def pick() -> None:
        d = current()
        if d and split.value not in d["splits"]:
            split.set(next(s for s in SPLITS if s in d["splits"]))
        await reload_runs()

    async def run() -> None:
        if not chosen.value:
            message.set("Pick at least one of your models.")
            return
        busy.set(True)
        try:
            for n, model_id in enumerate(chosen.value, start=1):
                label = f"{names.value.get(model_id, model_id)} ({n} of {len(chosen.value)})"
                message.set(f"Scoring {label}...")
                resp = await client.request("POST", "/bench/runs", json={
                    "model_id": model_id, "dataset_id": dataset_id.value, "split": split.value,
                    **({"limit": int(limit.value)} if limit.value else {})})
                if resp.status_code != 202:
                    message.set(f"Could not score {label}: {resp.json().get('detail')}")
                    return
                status = await wait_job(client, resp.json()["job_id"], 1.0)
                if status is None or status["state"] != "done":
                    reason = status["error"] if status else "it took too long"
                    message.set(f"Scoring {label} failed: {reason}")
                    return
                await reload_runs()
            message.set("Done. Newest runs are listed first.")
        finally:
            busy.set(False)

    def rows() -> list[dict]:
        ordered = sorted(runs.value, key=lambda r: -r["created_at"])
        return [run_row(r, names.value) for r in ordered]

    cards = [
        Card(title="Benchmark", children=[
            MultiSelect(chosen, options=lambda: [(m["id"], m["name"]) for m in models.value],
                        label="Models to score"),
            Select(dataset_id, options=lambda: [d["id"] for d in datasets.value],
                   label="Benchmark dataset", on_change=pick),
            Segmented(split, options=SPLITS, label="Benchmark split", on_change=pick),
            Number(limit, min=0, max=20000, step=1, label="Images (0 for all)"),
            Button("Run benchmark", on_click=run, disabled=busy),
            Text(lambda: message.value),
            Text("A `pipeline` run went through the slicer, detector and postprocess one image at "
                 "a time; an `in-process` run cut the same tiles and ran the model directly, so "
                 "its s/image is the model alone. Images without annotations are left out of "
                 "both.", markdown=True),
            Table(rows=rows, key="id", columns=COLUMNS, height="18rem"),
        ]),
    ]
    return cards, refresh
