"""The model view of the Explore tab: a stored benchmark run, scored the way the committed
reports are, and where the model is right or wrong on each image (ADR-0015, S2)."""

from common import Client, Select, Table, data_uri, fetch
from indah import Button, Card, Chart, Col, ImageOverlay, Row, Signal, Slider, Stat, Text

from geopipe.viewer import (
    filter_features,
    image_outcomes,
    match_image,
    match_overlay,
    prediction_features,
)

SCORERS = {"pipeline": "the full pipeline (slicer, detector, postprocess), scored by pycocotools "
                       "and geopipe.bench",
           "in-process": "the detector run directly, scored by pycocotools and geopipe.bench"}


def models_view(client: Client, on_deleted=None):
    """The view's components and a coroutine that loads the model list (after sign-in)."""
    models = Signal([])
    model_id = Signal("")
    runs = Signal([])  # runs of the selected model
    run_id = Signal("")
    run = Signal({})  # GET /runs/{id}
    coco = Signal({"images": [], "annotations": []})  # ground truth of the run's split
    preds = Signal({})  # file -> boxes
    threshold = Signal(0.5)
    selected = Signal(None)  # file shown below
    preview = Signal("")
    message = Signal("Sign in to explore models.")

    def metric(key: str):
        return lambda: f"{run.value['metrics'][key]:.3f}" if run.value else "-"

    async def open_image() -> None:
        info = next((i for i in coco.value["images"] if i["file_name"] == selected.value), None)
        if not info:
            return
        where = f"/datasets/{run.value['dataset_id']}/images/{run.value['split']}"
        resp = await client.request("GET", f"{where}/{info['file_name']}")
        if resp.status_code == 200:
            preview.set(data_uri(await fetch(resp.json()["url"]), "image/png"))

    async def load_run() -> None:
        found = next((r for r in runs.value if r["id"] == run_id.value), None)
        if not found:
            run.set({})
            return
        resp = await client.request("GET", f"/runs/{found['id']}")
        pred = await client.request("GET", f"/runs/{found['id']}/predictions")
        ann = await client.request(
            "GET", f"/datasets/{found['dataset_id']}/annotations/{found['split']}")
        if any(x.status_code != 200 for x in (resp, pred, ann)):
            message.set("Could not load that run.")
            return
        run.set(resp.json())
        preds.set(pred.json())
        coco.set(ann.json())
        message.set(f"{found['dataset_id']} / {found['split']}, "
                    f"{len(coco.value['images'])} images.")
        selected.set(rows()[0]["file"] if rows() else None)
        await open_image()

    async def load_model() -> None:
        resp = await client.request("GET", f"/runs?model_id={model_id.value}")
        runs.set(resp.json() if resp.status_code == 200 else [])
        run_id.set(runs.value[-1]["id"] if runs.value else "")
        if not runs.value:
            message.set("No benchmark run stored for this model yet.")
        await load_run()

    def own_model() -> bool:
        m = next((m for m in models.value if m["id"] == model_id.value), None)
        return bool(m and m["scope"] == "own")

    async def delete_model() -> None:
        if not own_model():
            return
        resp = await client.request("DELETE", f"/models/{model_id.value}")
        if resp.status_code != 200:
            message.set(f"Could not delete the model: {resp.json().get('detail')}")
            return
        gone = model_id.value
        model_id.set("")
        await load_models()
        message.set(f"Deleted {gone} and {resp.json()['runs_deleted']} run(s) scored for it.")
        if on_deleted:
            await on_deleted()

    async def load_models() -> None:
        resp = await client.request("GET", "/models")
        if resp.status_code != 200:
            return
        models.set([m for m in resp.json() if m["state"] == "ready"])
        if models.value and not any(m["id"] == model_id.value for m in models.value):
            model_id.set(models.value[0]["id"])
        await load_model()

    def rows() -> list[dict]:
        return image_outcomes(coco.value, preds.value, threshold.value) if run.value else []

    def current():
        file = selected.value
        info = next((i for i in coco.value["images"] if i["file_name"] == file), None)
        if not info:
            return None, [], []
        gt = [a["bbox"] for a in coco.value["annotations"] if a["image_id"] == info["id"]]
        feats = filter_features(prediction_features(preds.value.get(file, [])), threshold.value)
        return info, gt, feats

    def overlay() -> list[dict]:
        info, gt, feats = current()
        return match_overlay(match_image(gt, feats), info["width"], info["height"]) if info else []

    def image_text() -> str:
        info, gt, feats = current()
        if not info:
            return ""
        m = match_image(gt, feats)
        return (f"{info['file_name']}: {m['tp']} true positives (green), {m['fp']} false positives "
                f"(red), {m['fn']} missed (amber), out of {len(gt)} real boxes.")

    def totals() -> str:
        r = rows()
        tp, fp, fn = (sum(x[k] for x in r) for k in ("tp", "fp", "fn"))
        return (f"At score >= {threshold.value:.2f}, IoU >= 0.5: {tp} true positives, {fp} false "
                f"positives, {fn} missed. The cards above are fixed at score >= 0.5.")

    cards = [
        Card(title="Model", children=[
            Select(model_id, options=lambda: [m["id"] for m in models.value],
                   label="Inspect model",
                   on_change=load_model),
            Select(run_id, options=lambda: [r["id"] for r in runs.value], label="Run",
                   on_change=load_run),
            Button("Delete model", on_click=delete_model, variant="ghost",
                   confirm="Delete this model and the runs scored for it?",
                   disabled=lambda: not own_model()),
            Text(lambda: message.value),
            Text(lambda: (f"Scored by {SCORERS[run.value['scorer']]}. Detections kept at "
                          f"confidence >= {run.value['conf']}, matched at IoU {run.value['iou']} "
                          "for mAP.") if run.value else ""),
            Row(children=[
                Stat(metric("map"), label="mAP"), Stat(metric("map50"), label="mAP50"),
                Stat(metric("precision"), label="Precision"),
                Stat(metric("recall"), label="Recall"),
                Stat(metric("f1"), label="F1")], gap="1rem"),
            Chart(lambda: list(zip(run.value.get("pr_recall", []),
                                   run.value.get("pr_precision", []), strict=False)),
                  series=["precision"], title="Precision against recall (IoU 0.5)",
                  x_label="recall", y_label="precision", height=200)]),
        Card(title="Predicted against true boxes", children=[
            Slider(threshold, min=0, max=1, step=0.05, label="Score threshold"),
            Text(totals),
            ImageOverlay(preview, boxes=overlay, label_mode="hover",
                         alt="an image with true and predicted boxes"),
            Text(image_text),
            Table(rows=lambda: rows()[:50], key="file", selected=selected, height="18rem",
                  columns=[Col("file", "Worst images first"), Col("gt", "Real", kind="number"),
                           Col("tp", "TP", kind="number"), Col("fp", "FP", kind="number"),
                           Col("fn", "Missed", kind="number")],
                  on_change=open_image)]),
    ]
    return cards, load_models
