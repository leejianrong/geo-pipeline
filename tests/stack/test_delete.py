"""S7: delete datasets, models and runs through the API; things in use cannot go."""

import json
import time

import pytest
from conftest import BUCKET
from test_api import API, call
from test_bench_job import register_model
from test_explore import write_split

from geopipe import bus, registry, store
from geopipe.jobs import new_train_run

A, B = "dev-key-a", "dev-key-b"


def count_objects(s3, prefix) -> int:
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix)
    return sum(len(p.get("Contents", [])) for p in pages)


def delete(path, key=A):
    code, body = call("DELETE", f"{API}{path}", key)
    return code, json.loads(body) if body else {}


def make_dataset(tmp_path, dataset_id):
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    root = tmp_path / dataset_id
    write_split(root, "train", {"a.png": [[1, 1, 9, 9]]})
    write_split(root, "valid", {"c.png": [[2, 2, 8, 8]]})
    registry.register_dataset(
        s3,
        r,
        BUCKET,
        root,
        dataset_id,
        scope="acme",
        name=dataset_id,
        licence="CC BY 4.0",
        attribution="made up",
    )
    return r, s3


def stored_run(s3, r, run_id, model_id, dataset_id):
    run = registry.RunManifest(
        id=run_id,
        model_id=model_id,
        dataset_id=dataset_id,
        split="valid",
        scorer="in-process",
        conf=0.05,
        iou=0.65,
        created_at=time.time(),
        metrics={"map": 0.5},
    )
    registry.write_run(
        s3, r, BUCKET, run, {"pr_recall": [0.0], "pr_precision": [1.0]}, [], scope="acme"
    )


@pytest.fixture(autouse=True)
def clean():
    yield
    bus.connect().delete(
        "tenant:acme:datasets",
        "tenant:acme:models",
        "tenant:acme:runs",
        "tenant:acme:trainruns",
        "tenant:acme:benchjobs",
    )


def test_deleting_a_dataset_removes_its_files_listing_and_runs(tmp_path):
    r, s3 = make_dataset(tmp_path, "del-ds")
    register_model(s3, r, "del-model")
    stored_run(s3, r, "run-del-1", "del-model", "del-ds")
    assert count_objects(s3, "tenants/acme/datasets/del-ds/") > 5  # images, thumbs, annotations
    assert delete("/datasets/del-ds", B)[0] == 404  # not theirs

    code, body = delete("/datasets/del-ds")
    assert code == 200 and body == {"deleted": "del-ds", "runs_deleted": 1}
    assert count_objects(s3, "tenants/acme/datasets/del-ds/") == 0
    assert count_objects(s3, "tenants/acme/runs/run-del-1/") == 0
    assert (
        r.zrange("tenant:acme:datasets", 0, -1) == [] and r.zrange("tenant:acme:runs", 0, -1) == []
    )
    assert call("GET", f"{API}/datasets/del-ds", A)[0] == 404
    assert call("GET", f"{API}/models/del-model", A)[0] == 200  # the model stays
    assert delete("/datasets/del-ds")[0] == 404  # and it is not deleted twice


def test_built_in_datasets_models_and_runs_cannot_be_deleted(tmp_path):
    r, s3 = make_dataset(tmp_path, "mine")
    root = tmp_path / "shared-ds"
    write_split(root, "train", {"a.png": [[1, 1, 9, 9]]})
    registry.register_dataset(
        s3, r, BUCKET, root, "built-in-ds", name="Shared", licence="MIT", attribution="made up"
    )
    registry.register_model(
        s3,
        r,
        BUCKET,
        registry.ModelManifest(
            id="built-in-model",
            name="B",
            decoder="yolox",
            input_size=64,
            classes=["x"],
            licence="MIT",
            created_at=1.0,
            backend="stub",
        ),
    )
    run = registry.RunManifest(
        id="run-built-in",
        model_id="built-in-model",
        dataset_id="built-in-ds",
        split="train",
        scorer="pipeline",
        conf=0.05,
        iou=0.65,
        created_at=1.0,
        metrics={"map": 0.1},
    )
    registry.write_run(s3, r, BUCKET, run, {"pr_recall": [0.0], "pr_precision": [1.0]}, [])
    try:
        assert delete("/datasets/built-in-ds")[0] == 403
        assert delete("/models/built-in-model")[0] == 403
        assert delete("/runs/run-built-in")[0] == 403
        assert call("GET", f"{API}/datasets/built-in-ds", A)[0] == 200  # still there
    finally:
        for key in ("datasets:shared", "models:shared", "runs:shared"):
            r.zrem(key, "built-in-ds", "built-in-model", "run-built-in")
        registry.delete_prefix(s3, BUCKET, "shared/datasets/built-in-ds/")
        registry.delete_prefix(s3, BUCKET, "shared/models/built-in-model/")
        registry.delete_prefix(s3, BUCKET, "shared/runs/run-built-in/")


def test_a_dataset_or_model_in_use_cannot_be_deleted_until_the_job_ends(tmp_path):
    r, s3 = make_dataset(tmp_path, "busy-ds")
    register_model(s3, r, "busy-model")
    job = new_train_run(r, s3, BUCKET, "acme", "busy-ds", "fast", 1)  # queued, nobody running it
    code, body = delete("/datasets/busy-ds")
    assert code == 409 and f"training run {job.job_id}" in body["detail"]

    bus.set_status(
        r,
        "bench-busy",
        "running",
        tenant_id="acme",
        kind="bench",
        model_id="busy-model",
        dataset_id="busy-ds",
    )
    r.zadd("tenant:acme:benchjobs", {"bench-busy": time.time()})
    assert delete("/models/busy-model")[0] == 409
    assert "benchmark bench-busy" in delete("/datasets/busy-ds")[1]["detail"]

    bus.set_status(r, job.job_id, "cancelled", tenant_id="acme")
    bus.set_status(r, "bench-busy", "done", tenant_id="acme")
    assert delete("/models/busy-model")[0] == 200
    assert delete("/datasets/busy-ds")[0] == 200


def test_deleting_a_model_removes_the_file_and_the_runs_scored_for_it(tmp_path):
    r, s3 = make_dataset(tmp_path, "keep-ds")
    register_model(s3, r, "doomed")
    stored_run(s3, r, "run-doomed", "doomed", "keep-ds")
    assert count_objects(s3, "tenants/acme/models/doomed/") == 2  # model.onnx and the manifest
    assert delete("/models/doomed", B)[0] == 404
    assert delete("/models/doomed")[1] == {"deleted": "doomed", "runs_deleted": 1}
    assert count_objects(s3, "tenants/acme/models/doomed/") == 0
    assert call("GET", f"{API}/runs/run-doomed", A)[0] == 404
    assert call("GET", f"{API}/datasets/keep-ds", A)[0] == 200  # the dataset stays


def test_a_stored_run_and_an_ended_training_run_can_be_deleted(tmp_path):
    r, s3 = make_dataset(tmp_path, "runs-ds")
    register_model(s3, r, "runs-model")
    stored_run(s3, r, "run-one", "runs-model", "runs-ds")
    assert delete("/runs/run-one", B)[0] == 404
    assert delete("/runs/run-one")[0] == 200 and call("GET", f"{API}/runs/run-one", A)[0] == 404

    job = new_train_run(r, s3, BUCKET, "acme", "runs-ds", "fast", 1)
    code, body = delete(f"/train/runs/{job.job_id}")
    assert code == 409 and "cancel it first" in body["detail"]
    bus.set_status(r, job.job_id, "cancelled", tenant_id="acme")
    assert delete(f"/train/runs/{job.job_id}", B)[0] == 404
    assert delete(f"/train/runs/{job.job_id}")[0] == 200
    assert call("GET", f"{API}/train/runs/{job.job_id}", A)[0] == 404
    assert r.zrange("tenant:acme:trainruns", 0, -1) == []


def option_values(component) -> list[str]:
    return [o["value"] for o in component.reactive_props()["options"]()]


@pytest.mark.asyncio
async def test_the_buttons_delete_your_dataset_and_model_and_every_list_follows(tmp_path):
    from test_frontend import labelled, new_session, send, sign_in

    r, s3 = make_dataset(tmp_path, "ui-del-ds")
    register_model(s3, r, "ui-del-model")
    session = new_session("del-ui")
    await sign_in(session, "dev-key-a")
    datasets = labelled(session, "select", "Dataset")
    button = labelled(session, "button", "Delete dataset")
    await send(session, datasets, "change", {"value": "ui-del-ds"})
    assert button.reactive_props()["disabled"]() is False
    assert "ui-del-ds" in option_values(labelled(session, "select", "Training dataset"))
    assert "ui-del-ds" in option_values(labelled(session, "select", "Benchmark dataset"))

    await send(session, button, "click")
    for label in ("Dataset", "Training dataset", "Benchmark dataset"):
        assert "ui-del-ds" not in option_values(labelled(session, "select", label))
    assert call("GET", f"{API}/datasets/ui-del-ds", A)[0] == 404

    models = labelled(session, "select", "Inspect model")
    delete_model = labelled(session, "button", "Delete model")
    await send(session, models, "change", {"value": "ui-del-model"})
    assert delete_model.reactive_props()["disabled"]() is False
    await send(session, delete_model, "click")
    assert "ui-del-model" not in option_values(models)
    assert "user:ui-del-model" not in option_values(labelled(session, "select", "Model"))
    assert call("GET", f"{API}/models/ui-del-model", A)[0] == 404


@pytest.mark.asyncio
async def test_a_built_in_dataset_has_a_disabled_delete_button(tmp_path):
    from test_frontend import labelled, new_session, send, sign_in

    r, s3 = bus.connect(), store.client()
    root = tmp_path / "shared"
    write_split(root, "train", {"a.png": [[1, 1, 9, 9]]})
    registry.register_dataset(
        s3, r, BUCKET, root, "ui-built-in", name="S", licence="MIT", attribution="made up"
    )
    try:
        session = new_session("del-ui-b")
        await sign_in(session, "dev-key-a")
        await send(
            session, labelled(session, "select", "Dataset"), "change", {"value": "ui-built-in"}
        )
        assert labelled(session, "button", "Delete dataset").reactive_props()["disabled"]() is True
    finally:
        r.zrem("datasets:shared", "ui-built-in")
        registry.delete_prefix(s3, BUCKET, "shared/datasets/ui-built-in/")
