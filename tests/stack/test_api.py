"""V6: upload, submit, poll and download using only HTTP, as curl would."""

import json
import time
import urllib.error
import urllib.request

import pytest
from conftest import make_synth_tif

API = "http://api:8000"
# Presigned URLs are signed for the host-published store; inside the compose network we reach
# the same server by its service name and send the Host header the signature covers.
PUBLIC_HOST = "localhost:8333"


def call(method, url, key=None, body=None, data=None):
    req = urllib.request.Request(url, method=method, data=data)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-API-Key", key)
    if PUBLIC_HOST in url:
        req.full_url = url.replace(PUBLIC_HOST, "seaweedfs:8333")
        req.add_header("Host", PUBLIC_HOST)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wait_done(job_id, key):
    deadline = time.time() + 120
    while time.time() < deadline:
        _, body = call("GET", f"{API}/jobs/{job_id}", key)
        state = json.loads(body)["state"]
        if state in ("done", "incomplete", "error"):
            return state
        time.sleep(1)
    pytest.fail("job did not finish")


def test_upload_submit_poll_download_and_tenant_isolation(tmp_path):
    tif = tmp_path / "synth.tif"
    make_synth_tif(str(tif))
    status, body = call("POST", f"{API}/uploads", "dev-key-a")
    assert status == 200
    up = json.loads(body)
    assert call("PUT", up["upload_url"], data=tif.read_bytes())[0] == 200

    status, body = call("POST", f"{API}/jobs", "dev-key-a", {"key": up["key"]})
    assert status == 202
    job_id = json.loads(body)["job_id"]
    assert wait_done(job_id, "dev-key-a") == "done"

    status, body = call("GET", f"{API}/jobs/{job_id}/result", "dev-key-a")
    assert status == 200
    status, body = call("GET", json.loads(body)["url"])
    assert status == 200
    assert len(json.loads(body)["features"]) == 5  # the five rectangles in the synthetic raster

    # the other tenant can neither see the job nor submit the first tenant's upload
    assert call("GET", f"{API}/jobs/{job_id}", "dev-key-b")[0] == 404
    assert call("GET", f"{API}/jobs/{job_id}/result", "dev-key-b")[0] == 404
    assert call("POST", f"{API}/jobs", "dev-key-b", {"key": up["key"]})[0] == 403
    assert call("GET", f"{API}/jobs/{job_id}")[0] == 401


def test_submit_before_upload_is_404():
    _, body = call("POST", f"{API}/uploads", "dev-key-a")
    status, _ = call("POST", f"{API}/jobs", "dev-key-a", {"key": json.loads(body)["key"]})
    assert status == 404


def test_browser_uploads_are_allowed_from_the_frontend_origin_only():
    status, body = call("POST", f"{API}/uploads", "dev-key-a")
    assert status == 200
    url = json.loads(body)["upload_url"].replace(PUBLIC_HOST, "seaweedfs:8333")

    def preflight(origin):
        req = urllib.request.Request(url, method="OPTIONS", headers={
            "Origin": origin, "Access-Control-Request-Method": "PUT", "Host": PUBLIC_HOST})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.headers.get("Access-Control-Allow-Origin")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Access-Control-Allow-Origin")

    assert preflight("http://localhost:8080") == (200, "http://localhost:8080")
    assert preflight("http://evil.example") == (403, None)
