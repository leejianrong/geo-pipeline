"""S2: the model view shows a stored run through the indah session, against the real API."""

import pytest
from conftest import BUCKET
from test_explore import BOXES, value, write_split
from test_frontend import labelled, new_session, nodes, send, sign_in

from geopipe import bus, registry, store

METRICS = {k: 0.0 for k in registry.METRIC_KEYS} | {
    "map": 0.4567,
    "map50": 0.8,
    "precision": 0.5,
    "recall": 2 / 3,
    "f1": 4 / 7,
}
PREDS = [  # image 1 (t1.png): two hits, one false alarm; image 2 (t2.png) is empty
    {"image_id": 1, "category_id": 1, "bbox": [2, 3, 20, 10], "score": 0.9},
    {"image_id": 1, "category_id": 1, "bbox": [30, 5, 10, 10], "score": 0.7},
    {"image_id": 1, "category_id": 1, "bbox": [0, 30, 6, 6], "score": 0.3},
]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    root = tmp_path_factory.mktemp("models")
    write_split(root, "test", BOXES)
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    registry.register_dataset(
        s3,
        r,
        BUCKET,
        root,
        "mv-data",
        scope="acme",
        name="Mv",
        licence="CC BY-SA 4.0",
        attribution="made up",
    )
    registry.register_model(
        s3,
        r,
        BUCKET,
        registry.ModelManifest(
            id="mv-model",
            name="Mv",
            decoder="yolox",
            input_size=416,
            classes=["building"],
            licence="MIT",
            created_at=1.0,
        ),
        scope="acme",
    )
    manifest = registry.RunManifest(
        id="mv-run",
        model_id="mv-model",
        dataset_id="mv-data",
        split="test",
        scorer="pipeline",
        conf=0.05,
        iou=0.65,
        metrics=METRICS,
        created_at=1.0,
    )
    registry.write_run(
        s3,
        r,
        BUCKET,
        manifest,
        {"pr_recall": [0.0, 0.5, 1.0], "pr_precision": [1.0, 0.9, 0.5]},
        PREDS,
        scope="acme",
    )
    yield
    for key in ("tenant:acme:datasets", "tenant:acme:models", "tenant:acme:runs"):
        r.delete(key)  # other stack tests list these


@pytest.mark.asyncio
async def test_the_model_view_shows_saved_metrics_and_boxes_at_a_threshold(run):
    session = new_session("models-a")
    await sign_in(session, "dev-key-a")
    assert value(labelled(session, "select", "Inspect model")) == "mv-model"
    assert value(labelled(session, "select", "Run")) == "mv-run"
    assert value(labelled(session, "stat", "mAP")) == "0.457"
    assert value(labelled(session, "stat", "F1")) == "0.571"
    chart = next(c for c in nodes(session, "chart") if "Precision" in c.static_props()["title"])
    assert value(chart, "data") == [[0.0, 1.0], [0.5, 0.9], [1.0, 0.5]]

    overlay = nodes(session, "imageoverlay")[-1]
    assert value(overlay, "src").startswith("data:image/png;base64,")
    labels = [b["label"] for b in value(overlay, "boxes")]
    assert sorted(labels) == ["TP 0.70", "TP 0.90", "missed"]  # default threshold 0.5

    slider = labelled(session, "slider", "Score threshold")
    await send(session, slider, "input", {"value": 0.2})
    assert sorted(b["label"] for b in value(overlay, "boxes")) == [
        "FP 0.30",
        "TP 0.70",
        "TP 0.90",
        "missed",
    ]
    await send(session, slider, "input", {"value": 0.8})
    assert sorted(b["label"] for b in value(overlay, "boxes")) == ["TP 0.90", "missed", "missed"]

    table = next(
        t
        for t in nodes(session, "table")
        if any(c["title"] == "Worst images first" for c in t.static_props()["columns"])
    )
    files = [r["file"] for r in value(table, "rows")]
    assert files == ["t1.png", "t2.png"]  # t1 has errors, t2 has none
    await send(session, table, "select", {"key": "t2.png"})
    assert value(overlay, "boxes") == []


@pytest.mark.asyncio
async def test_another_tenant_sees_no_acme_model_or_run(run):
    session = new_session("models-b")
    await sign_in(session, "dev-key-b")
    assert "mv-model" not in str(value(labelled(session, "select", "Inspect model"), "options"))
