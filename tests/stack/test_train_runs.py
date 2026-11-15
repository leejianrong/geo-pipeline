"""S4: a training run goes through the API to the trainer; the small image refuses it at once, and
one tenant trains one run at a time (the handler runs here with a fake training function)."""

import json
import threading
import time

import pytest
from conftest import BUCKET
from test_api import API, call
from test_explore import write_split

from geopipe import bus, registry, store
from geopipe.jobs import new_train_run
from geopipe.messages import TrainJob
from geopipe.train import Cancelled, handle_train

A, B = "dev-key-a", "dev-key-b"


@pytest.fixture(scope="module", autouse=True)
def train_only_dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("train-only")
    write_split(root, "train", {"a.png": [[1, 1, 9, 9]]})
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    registry.register_dataset(
        s3,
        r,
        BUCKET,
        root,
        "train-only",
        scope="acme",
        name="T1",
        licence="CC BY 4.0",
        attribution="made up",
    )
    yield


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("train")
    write_split(root, "train", {"a.png": [[1, 1, 9, 9]], "b.png": []})
    write_split(root, "valid", {"c.png": [[2, 2, 8, 8]]})
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    registry.register_dataset(
        s3,
        r,
        BUCKET,
        root,
        "train-demo",
        scope="acme",
        name="T",
        licence="CC BY 4.0",
        attribution="made up",
    )
    yield "train-demo"
    r.delete("tenant:acme:datasets", "tenant:acme:trainruns")  # other stack tests list these


def get(path, key=A):
    code, body = call("GET", f"{API}{path}", key)
    return code, json.loads(body) if code == 200 else None


def wait_for(run_id, states, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        run = get(f"/train/runs/{run_id}")[1]
        if run["state"] in states:
            return run
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} is {run['state']}")


def test_the_cpu_trainer_refuses_a_run_at_once_with_the_reason(dataset):
    """A trainer with no training function (the small CPU image) fails a queued run at once."""
    r, s3 = bus.connect(), store.client()
    job = new_train_run(r, s3, BUCKET, "acme", dataset, "fast")
    with pytest.raises(bus.PermanentError, match="GPU trainer image"):
        handle_train(r, s3, job, None)
    run = get(f"/train/runs/{job.job_id}")[1]
    assert run["state"] == "failed" and "make up-gpu" in run["error"] and run["epochs"] == 60
    assert [x["id"] for x in get("/train/runs")[1]] == [job.job_id]
    assert get(f"/train/runs/{job.job_id}", B)[0] == 404
    assert get("/train/runs", B)[1] == []


def test_the_fake_trainer_runs_the_whole_flow_through_the_live_stack(dataset):
    """`make e2e` runs the trainer with TRAINER_DEVICE=fake: start, progress, a model."""
    code, body = call(
        "POST", f"{API}/train/runs", A, {"dataset_id": dataset, "preset": "fast", "epochs": 3}
    )
    assert code == 202
    run = wait_for(json.loads(body)["run_id"], {"done"})
    assert run["model_id"].startswith("model-") and [
        h["epoch"] for h in get(f"/train/runs/{run['id']}")[1]["history"]
    ] == [1, 2, 3]
    assert run["model_id"] in [m["id"] for m in get("/models")[1]]
    r = bus.connect()
    r.delete("tenant:acme:models")


def test_bad_requests_are_refused_before_anything_is_queued(dataset):
    def start(key=A, **kw):
        body = {"dataset_id": dataset, "preset": "fast", **kw}
        return call("POST", f"{API}/train/runs", key, body)[0]

    assert start(preset="huge") == 422
    assert start(dataset_id="nope") == 422
    assert start(key=B) == 422  # another tenant's dataset
    assert start(dataset_id="train-only") == 422  # needs a valid or test split too
    assert start(epochs=0) == 422 and start(epochs=10_000) == 422
    assert start(key=None) == 401
    assert [p["name"] for p in get("/train/presets")[1]] == ["fast", "balanced", "accurate", "best"]


def test_a_tenant_trains_one_run_at_a_time(dataset):
    r, s3 = bus.connect(), store.client()
    jobs = [new_train_run(r, s3, BUCKET, "acme", dataset, "fast", 1) for _ in range(2)]
    first, second = (j.job_id for j in jobs)
    release, started = threading.Event(), []

    def fake(job, ctx):
        started.append(job.job_id)
        ctx.progress(1, val_ap=0.5)
        assert release.wait(30)
        return f"model-{job.job_id}"

    def run(job):
        handle_train(r, s3, job, fake, poll=0.1)

    def until(check, timeout=10):
        end = time.time() + timeout
        while not check() and time.time() < end:
            time.sleep(0.05)

    threads = [threading.Thread(target=run, args=(j,)) for j in jobs]
    threads[0].start()
    until(lambda: first in started)
    threads[1].start()
    until(lambda: r.hget(f"job:{second}:status", "state") == "waiting")
    assert r.hget(f"job:{second}:status", "state") == "waiting" and started == [first]
    assert r.hget(f"job:{first}:status", "val_ap") == "0.5"
    assert r.get("tenant:acme:training") == first

    release.set()
    for t in threads:
        t.join(30)
    assert started == [first, second]  # the second ran only after the first ended
    assert r.hget(f"job:{second}:status", "state") == "done"
    assert r.get("tenant:acme:training") is None
    run = registry.find_train_run(s3, BUCKET, "acme", second)
    assert run.state == "done" and run.model_id == f"model-{second}"


def start_fake(r, s3, dataset, fake, *, preset="fast"):
    """Queue a run and run its handler in a thread with `fake`, the way the trainer would."""
    job = new_train_run(r, s3, BUCKET, "acme", dataset, preset, 3)
    thread = threading.Thread(target=handle_train, args=(r, s3, job, fake), kwargs={"poll": 0.1})
    thread.start()
    return job.job_id, thread


def test_progress_history_and_no_stall_deadline(dataset):
    r, s3 = bus.connect(), store.client()
    seen = threading.Event()

    def fake(job, ctx):
        for epoch, ap in ((1, None), (2, 0.31), (3, 0.4)):
            ctx.progress(epoch, val_ap=ap, loss=5.0 - epoch, epochs=3)
        seen.set()
        return "m"

    run_id, thread = start_fake(r, s3, dataset, fake)
    thread.join(30)
    run = get(f"/train/runs/{run_id}")[1]
    assert run["state"] == "done" and run["model_id"] == "m"
    assert run["history"] == [
        {"epoch": 1, "loss": 4.0},
        {"epoch": 2, "loss": 3.0, "val_ap": 0.31},
        {"epoch": 3, "loss": 2.0, "val_ap": 0.4},
    ]
    # a training or ingest job must never sit on the detection sweeper's stall list
    assert r.zscore("jobs:deadlines", run_id) is None


def test_cancel_stops_a_running_run_and_frees_the_slot(dataset):
    r, s3 = bus.connect(), store.client()
    running = threading.Event()

    def fake(job, ctx):
        running.set()
        while not ctx.cancelled():
            time.sleep(0.05)
        raise Cancelled

    run_id, thread = start_fake(r, s3, dataset, fake)
    assert running.wait(10)
    assert call("POST", f"{API}/train/runs/{run_id}/cancel", B)[0] == 404
    assert call("POST", f"{API}/train/runs/{run_id}/cancel", A)[0] == 202
    thread.join(15)
    assert not thread.is_alive()
    run = get(f"/train/runs/{run_id}")[1]
    assert run["state"] == "cancelled"
    assert r.get("tenant:acme:training") is None
    assert call("POST", f"{API}/train/runs/{run_id}/cancel", A)[0] == 409  # already over


def test_cancelling_a_waiting_run_removes_it_from_the_queue(dataset):
    r, s3 = bus.connect(), store.client()
    release, running = threading.Event(), threading.Event()

    def blocker(job, ctx):
        running.set()
        assert release.wait(30)
        return "m"

    def never(job, ctx):
        raise AssertionError("a cancelled run must not start")

    first, t1 = start_fake(r, s3, dataset, blocker)
    assert running.wait(10)
    second, t2 = start_fake(r, s3, dataset, never)
    deadline = time.time() + 10
    while r.hget(f"job:{second}:status", "state") != "waiting" and time.time() < deadline:
        time.sleep(0.05)
    assert call("POST", f"{API}/train/runs/{second}/cancel", A)[0] == 202
    t2.join(15)
    assert get(f"/train/runs/{second}")[1]["state"] == "cancelled"
    assert r.get("tenant:acme:training") == first  # the first run still holds it
    release.set()
    t1.join(30)
    assert get(f"/train/runs/{first}")[1]["state"] == "done"


def test_a_run_cancelled_before_it_starts_never_runs(dataset):
    r, s3 = bus.connect(), store.client()
    job = new_train_run(r, s3, BUCKET, "acme", dataset, "fast", 1)
    run_id = job.job_id
    assert call("POST", f"{API}/train/runs/{run_id}/cancel", A)[0] == 202
    handle_train(r, s3, job, lambda j, c: pytest.fail("started"))
    assert get(f"/train/runs/{run_id}")[1]["state"] == "cancelled"


def test_download_and_register_a_model_through_the_real_store(dataset, tmp_path):
    from geopipe.runner import download_dataset, register_model

    r, s3 = bus.connect(), store.client()
    got = download_dataset(s3, BUCKET, "acme", dataset, tmp_path / "data")
    assert sorted(p.name for p in got.iterdir()) == ["train", "valid"]  # no thumbnails
    assert (got / "train" / "a.png").is_file() and (
        got / "valid" / "_annotations.coco.json"
    ).is_file()

    onnx = tmp_path / "m.onnx"
    onnx.write_bytes(b"onnx-bytes")
    manifest = registry.find(s3, BUCKET, "acme", dataset)[1]
    job = TrainJob(
        tenant_id="acme",
        job_id="train-abc123",
        bucket=BUCKET,
        dataset_id=dataset,
        preset="balanced",
        epochs=3,
    )
    model_id = register_model(s3, r, BUCKET, job, manifest, onnx)
    assert model_id == "model-abc123"
    stored = s3.get_object(Bucket=BUCKET, Key="tenants/acme/models/model-abc123/model.onnx")
    assert stored["Body"].read() == b"onnx-bytes"
    listed = {m["id"]: m for m in get("/models")[1]}["model-abc123"]
    assert listed["decoder"] == "yolox" and listed["input_size"] == 640
    assert listed["trained_on"] == dataset and listed["run_id"] == "train-abc123"
    assert listed["licence"] == "CC BY 4.0" and listed["scope"] == "own"
    assert "model-abc123" not in [m["id"] for m in get("/models", "dev-key-b")[1]]
    r.delete("tenant:acme:models")
