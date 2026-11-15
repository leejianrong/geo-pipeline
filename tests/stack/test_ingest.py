"""S3: the trainer stage takes an ingest job from the API and reports bad input permanently."""

import json
import time

import pytest
from test_api import API, call

from geopipe import bus

A, B = "dev-key-a", "dev-key-b"
FORM = {"name": "My Roofs", "licence": "CC BY 4.0", "attribution": "me"}


@pytest.fixture(autouse=True)
def forget_datasets():
    yield
    bus.connect().delete("tenant:acme:datasets")  # other stack tests list this tenant's datasets


def wait_for_state(job_id, key, states, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = call("GET", f"{API}/jobs/{job_id}", key)
        status = json.loads(body)
        if status["state"] in states:
            return status
        time.sleep(0.5)
    raise AssertionError(f"job {job_id} stuck in {status['state']}")


def test_a_missing_upload_fails_at_once_with_a_clear_report():
    key = "tenants/acme/uploads/datasets/not-there.zip"
    code, body = call("POST", f"{API}/datasets", A, {**FORM, "upload_key": key})
    assert code == 202
    ids = json.loads(body)
    assert ids["dataset_id"].startswith("my-roofs-")

    status = wait_for_state(ids["job_id"], A, {"error"})
    assert "upload not found" in status["error"] and key in status["error"]

    _, body = call("GET", f"{API}/datasets/{ids['dataset_id']}", A)
    manifest = json.loads(body)
    assert manifest["state"] == "failed" and "upload not found" in manifest["report"]
    assert manifest["scope"] == "own" and manifest["licence"] == "CC BY 4.0"

    # the report belongs to its owner
    assert call("GET", f"{API}/datasets/{ids['dataset_id']}", B)[0] == 404
    assert call("GET", f"{API}/jobs/{ids['job_id']}", B)[0] == 404
    _, listing = call("GET", f"{API}/datasets", B)
    assert ids["dataset_id"] not in [d["id"] for d in json.loads(listing)]


def test_only_your_own_dataset_uploads_can_be_ingested():
    for key in ("tenants/globex/uploads/datasets/x.zip", "tenants/acme/uploads/x.tif",
                "tenants/acme/uploads/datasets/../../x.zip", "shared/datasets/x.zip"):
        code, _ = call("POST", f"{API}/datasets", A, {**FORM, "upload_key": key})
        assert code == 403, key
    code, _ = call("POST", f"{API}/datasets", A,
                   {**FORM, "licence": "", "upload_key": "tenants/acme/uploads/datasets/x.zip"})
    assert code == 422  # a licence is required (ADR-0016)
