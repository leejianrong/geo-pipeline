"""S6: an in-process benchmark of a registered model through the API and the trainer."""

import json

import pytest
from conftest import BUCKET
from test_api import API, call
from test_explore import write_split
from test_user_model import constant_yolox, wait

from geopipe import bus, registry, store

A, B = "dev-key-a", "dev-key-b"
# The constant model's box in a 64 x 48 image: centre (20, 28), 16 px square, as x, y, w, h.
BOX = [12, 20, 16, 16]


def register_model(s3, r, model_id, classes=("building",), builtin=False):
    registry.register_model(
        s3,
        r,
        BUCKET,
        registry.ModelManifest(
            id=model_id,
            name=model_id,
            decoder="yolox",
            input_size=64,
            classes=list(classes),
            licence="MIT",
            created_at=1.0,
            backend="yolox-tiny-buildings" if builtin else None,
        ),
        scope="acme",
    )
    s3.put_object(
        Bucket=BUCKET, Key=f"tenants/acme/models/{model_id}/model.onnx", Body=constant_yolox()
    )


@pytest.fixture(scope="module", autouse=True)
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("benchjob")
    write_split(root, "test", {"a.png": [BOX], "b.png": [BOX]})
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    registry.register_dataset(
        s3,
        r,
        BUCKET,
        root,
        "bench-ds",
        scope="acme",
        name="B",
        licence="CC BY 4.0",
        attribution="made up",
    )
    register_model(s3, r, "bench-const")
    register_model(s3, r, "bench-cars", classes=("car",))
    register_model(s3, r, "bench-builtin", builtin=True)
    yield
    r.delete("tenant:acme:datasets", "tenant:acme:models", "tenant:acme:runs")


def start(model="bench-const", dataset="bench-ds", split="test", key=A, **kw):
    return call(
        "POST",
        f"{API}/bench/runs",
        key,
        {"model_id": model, "dataset_id": dataset, "split": split, **kw},
    )


def test_a_benchmark_scores_the_model_and_stores_a_run_the_explore_view_opens():
    code, body = start()
    assert code == 202
    status = wait(bus.connect(), json.loads(body)["job_id"], {"done", "error"})
    assert status["state"] == "done" and status["done"] == "2"
    run = json.loads(call("GET", f"{API}/runs/{status['run_id']}", A)[1])
    assert run["scorer"] == "in-process" and run["model_id"] == "bench-const"
    assert run["metrics"]["map50"] == 1.0 and run["metrics"]["tp"] == 2.0
    assert run["metrics"]["fp"] == 0.0 and run["metrics"]["fn"] == 0.0
    assert run["pr_recall"] and run["scope"] == "own"
    preds = json.loads(call("GET", f"{API}/runs/{run['id']}/predictions", A)[1])
    assert sorted(preds) == ["a.png", "b.png"]
    assert [round(v) for v in preds["a.png"][0]["bbox"]] == BOX
    listed = json.loads(call("GET", f"{API}/runs?model_id=bench-const", A)[1])
    assert run["id"] in [x["id"] for x in listed]
    assert call("GET", f"{API}/runs/{run['id']}", B)[0] == 404


def test_a_limit_scores_only_the_first_images():
    code, body = start(limit=1)
    status = wait(bus.connect(), json.loads(body)["job_id"], {"done", "error"})
    run = json.loads(call("GET", f"{API}/runs/{status['run_id']}", A)[1])
    assert status["done"] == "1" and run["metrics"]["tp"] == 1.0


def test_requests_that_cannot_work_are_refused_or_fail_with_a_reason():
    assert start(model="nope")[0] == 422
    assert start(model="bench-builtin")[0] == 422  # a built-in model has no ONNX to run
    assert start(split="valid")[0] == 422
    assert start(dataset="nope")[0] == 422
    assert start(key=B)[0] == 422  # another tenant's model and dataset
    assert start(limit=0)[0] == 422
    assert call("POST", f"{API}/bench/runs", None, {})[0] == 401
    code, body = start(model="bench-cars")  # the model detects cars, the dataset has buildings
    status = wait(bus.connect(), json.loads(body)["job_id"], {"done", "error"})
    assert status["state"] == "error" and "share none" in status["error"]
