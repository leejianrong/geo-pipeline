"""GEO-41: finished jobs survive Valkey forgetting them (a restart, or the status TTL)."""

import json
import urllib.error
import urllib.request

import pytest

from geopipe import bus, history

API = "http://api:8000"
A, B = "dev-key-a", "dev-key-b"  # tenants acme and globex


def get(path, key):
    req = urllib.request.Request(API + path, headers={"X-API-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, None


@pytest.fixture
def forgotten(stack):
    """A finished job whose Valkey status has been deleted."""
    r = stack[0]
    job_id = "history-test-job"
    r.delete(f"job:{job_id}:status")
    bus.set_status(r, job_id, "done", tenant_id="acme", kind="detect",
                   output_uri="s3://imagery/tenants/acme/results/h.geojson")
    r.delete(f"job:{job_id}:status")
    return job_id


def test_the_database_has_the_job_and_the_api_answers_from_it(forgotten):
    assert history.from_env().get(forgotten, "acme")["state"] == "done"
    status, body = get(f"/jobs/{forgotten}", A)
    assert status == 200 and body["state"] == "done" and body["tenant_id"] == "acme"
    assert get(f"/jobs/{forgotten}", B)[0] == 404
    assert forgotten in [j["job_id"] for j in get("/jobs", A)[1]]
    assert forgotten not in [j["job_id"] for j in get("/jobs", B)[1]]
