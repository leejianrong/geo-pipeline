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
from geopipe.jobs import submit_train
from geopipe.messages import TrainJob
from geopipe.train import handle_train

A, B = "dev-key-a", "dev-key-b"


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("train")
    write_split(root, "train", {"a.png": [[1, 1, 9, 9]], "b.png": []})
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    registry.register_dataset(
        s3, r, BUCKET, root, "train-demo", scope="acme", name="T", licence="CC BY 4.0",
        attribution="made up")
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
    code, body = call("POST", f"{API}/train/runs", A, {"dataset_id": dataset, "preset": "fast"})
    assert code == 202
    run_id = json.loads(body)["run_id"]
    run = wait_for(run_id, {"failed"})
    assert "GPU trainer image" in run["error"] and run["epochs"] == 60  # the preset's default
    status = get(f"/jobs/{run_id}")[1]
    assert status["state"] == "error" and "make up-gpu" in status["error"]
    assert [x["id"] for x in get("/train/runs")[1]] == [run_id]
    assert get(f"/train/runs/{run_id}", B)[0] == 404
    assert get("/train/runs", B)[1] == []


def test_bad_requests_are_refused_before_anything_is_queued(dataset):
    def start(key=A, **kw):
        body = {"dataset_id": dataset, "preset": "fast", **kw}
        return call("POST", f"{API}/train/runs", key, body)[0]

    assert start(preset="huge") == 422
    assert start(dataset_id="nope") == 422
    assert start(key=B) == 422  # another tenant's dataset
    assert start(epochs=0) == 422 and start(epochs=10_000) == 422
    assert start(key=None) == 401
    assert [p["name"] for p in get("/train/presets")[1]] == ["fast", "balanced", "accurate", "best"]


def test_a_tenant_trains_one_run_at_a_time(dataset):
    r, s3 = bus.connect(), store.client()
    first, second = (submit_train(r, s3, BUCKET, "acme", dataset, "fast", 1) for _ in range(2))
    release, started = threading.Event(), []

    def fake(job, ctx):
        started.append(job.job_id)
        ctx.progress(1, val_ap=0.5)
        assert release.wait(30)
        return f"model-{job.job_id}"

    def run(run_id):
        job = TrainJob(
            tenant_id="acme", job_id=run_id, bucket=BUCKET, dataset_id=dataset, preset="fast",
            epochs=1)
        handle_train(r, s3, job, fake, poll=0.1)

    def until(check, timeout=10):
        end = time.time() + timeout
        while not check() and time.time() < end:
            time.sleep(0.05)

    threads = [threading.Thread(target=run, args=(i,)) for i in (first, second)]
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
