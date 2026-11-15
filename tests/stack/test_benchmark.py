"""S6: the Train tab's Benchmark section through the indah session and the live trainer."""

import pytest
from conftest import BUCKET
from test_bench_job import BOX, register_model
from test_explore import write_split
from test_frontend import labelled, new_session, nodes, send, sign_in

from geopipe import bus, registry, store


def value(component, prop="value"):
    return component.reactive_props()[prop]()


@pytest.fixture(scope="module", autouse=True)
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("benchui")
    write_split(root, "test", {"a.png": [BOX], "b.png": [BOX]})
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    registry.register_dataset(
        s3,
        r,
        BUCKET,
        root,
        "bench-ui",
        scope="acme",
        name="BU",
        licence="CC BY 4.0",
        attribution="made up",
    )
    register_model(s3, r, "bench-ui-model")
    # an earlier pipeline run of another model on the same split, to compare against
    registry.register_model(
        s3,
        r,
        BUCKET,
        registry.ModelManifest(
            id="piped",
            name="Piped YOLOX",
            decoder="yolox",
            input_size=640,
            classes=["building"],
            licence="MIT",
            created_at=1.0,
            backend="yolox-tiny-buildings",
        ),
        scope="acme",
    )
    run = registry.RunManifest(
        id="run-piped",
        model_id="piped",
        dataset_id="bench-ui",
        split="test",
        scorer="pipeline",
        conf=0.05,
        iou=0.65,
        created_at=1.0,
        metrics={k: 0.5 for k in registry.METRIC_KEYS} | {"tp": 1.0, "fn": 1.0},
    )
    registry.write_run(
        s3, r, BUCKET, run, {"pr_recall": [0.0], "pr_precision": [1.0]}, [], scope="acme"
    )
    yield
    r.delete("tenant:acme:datasets", "tenant:acme:models", "tenant:acme:runs")


def texts(session):
    return [str(value(t, "text")) for t in nodes(session, "text") if "text" in t.reactive_props()]


def bench_table(session):
    return next(
        t
        for t in nodes(session, "table")
        if any(c["title"] == "mAP50" for c in t.static_props()["columns"])
    )


@pytest.mark.asyncio
async def test_benchmark_a_model_and_compare_it_with_an_earlier_run():
    session = new_session("bench-a")
    await sign_in(session, "dev-key-a")
    pick = labelled(session, "select", "Benchmark dataset")
    await send(session, pick, "change", {"value": "bench-ui"})
    models = labelled(session, "multiselect", "Models to score")
    assert [o["value"] for o in value(models, "options")] == ["bench-ui-model"]  # yours, ONNX only
    rows = value(bench_table(session), "rows")
    assert [(r["model"], r["scorer"]) for r in rows] == [("Piped YOLOX", "pipeline")]

    await send(session, labelled(session, "button", "Run benchmark"), "click")
    assert "Pick at least one of your models." in texts(session)

    await send(session, models, "change", {"value": ["bench-ui-model"]})
    await send(session, labelled(session, "button", "Run benchmark"), "click")  # waits for it
    assert "Done. Newest runs are listed first." in texts(session)
    rows = value(bench_table(session), "rows")
    assert [(r["model"], r["scorer"]) for r in rows] == [
        ("bench-ui-model", "in-process"),
        ("Piped YOLOX", "pipeline"),
    ]
    fresh = rows[0]
    assert fresh["map50"] == "1.000" and fresh["images"] == 2 and fresh["precision"] == "1.000"
    assert float(fresh["speed"]) >= 0


@pytest.mark.asyncio
async def test_another_tenant_sees_neither_models_nor_runs_here():
    session = new_session("bench-b")
    await sign_in(session, "dev-key-b")
    models = labelled(session, "multiselect", "Models to score")
    assert value(models, "options") == []
    assert value(bench_table(session), "rows") == []
