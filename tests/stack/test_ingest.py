"""S3: the trainer stage takes an ingest job from the API and reports bad input permanently."""

import io
import json
import time
import zipfile

import pytest
from PIL import Image
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
    assert bus.connect().zscore("jobs:deadlines", ids["job_id"]) is None  # not a sweeper job
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
    for key in (
        "tenants/globex/uploads/datasets/x.zip",
        "tenants/acme/uploads/x.tif",
        "tenants/acme/uploads/datasets/../../x.zip",
        "shared/datasets/x.zip",
    ):
        code, _ = call("POST", f"{API}/datasets", A, {**FORM, "upload_key": key})
        assert code == 403, key
    code, _ = call(
        "POST",
        f"{API}/datasets",
        A,
        {**FORM, "licence": "", "upload_key": "tenants/acme/uploads/datasets/x.zip"},
    )
    assert code == 422  # a licence is required (ADR-0016)


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 120, 60)).save(out, "PNG")
    return out.getvalue()


def make_zip(entries: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            zf.writestr(name, json.dumps(body) if isinstance(body, dict) else body)
    return out.getvalue()


def coco(names, boxes):
    images = [
        {"id": n, "file_name": f, "width": 64, "height": 48} for n, f in enumerate(names, start=1)
    ]
    anns = [
        {"id": n * 10 + k, "image_id": n, "category_id": 1, "bbox": b, "iscrowd": 0}
        for n, f in enumerate(names, start=1)
        for k, b in enumerate(boxes.get(f, []))
    ]
    return {"images": images, "annotations": anns, "categories": [{"id": 1, "name": "roof"}]}


def ingest(data: bytes, key=A):
    """Upload through a presigned URL and queue it, the way the browser does."""
    _, body = call("POST", f"{API}/uploads/dataset", key)
    up = json.loads(body)
    assert call("PUT", up["upload_url"], data=data)[0] == 200
    code, body = call("POST", f"{API}/datasets", key, {**FORM, "upload_key": up["key"]})
    assert code == 202
    ids = json.loads(body)
    return ids, wait_for_state(ids["job_id"], key, {"done", "error"})


def test_a_good_zip_becomes_a_ready_dataset_the_explore_api_serves():
    zip_bytes = make_zip(
        {
            "train/a.png": png(),
            "train/b.png": png(),
            "train/_annotations.coco.json": coco(["a.png", "b.png"], {"a.png": [[1, 1, 9, 9]]}),
            "valid/c.png": png(),
            "valid/_annotations.coco.json": coco(
                ["c.png"], {"c.png": [[2, 2, 8, 8], [20, 9, 5, 5]]}
            ),
        }
    )
    ids, status = ingest(zip_bytes)
    assert status["state"] == "done" and status["images"] == "3" and status["boxes"] == "3"
    assert bus.connect().zscore("jobs:deadlines", ids["job_id"]) is None  # not a sweeper job

    _, body = call("GET", f"{API}/datasets/{ids['dataset_id']}", A)
    m = json.loads(body)
    assert m["state"] == "ready" and m["splits"] == {"train": 2, "valid": 1}
    assert m["classes"] == ["roof"] and m["licence"] == "CC BY 4.0" and m["scope"] == "own"
    _, body = call("GET", f"{API}/datasets/{ids['dataset_id']}/images/valid", A)
    page = json.loads(body)
    assert [(i["file"], i["boxes"]) for i in page] == [("c.png", 2)]
    assert call("GET", page[0]["thumb_url"])[0] == 200  # the thumbnail was written
    assert call("GET", f"{API}/datasets/{ids['dataset_id']}", B)[0] == 404


@pytest.mark.parametrize(
    "entries, expected",
    [
        ({"../evil.png": "x"}, "unsafe path"),
        (
            {"train/a.png": "not an image", "train/_annotations.coco.json": coco(["a.png"], {})},
            "unreadable image",
        ),
        ({"train/_annotations.coco.json": coco(["gone.png"], {})}, "train/gone.png"),
        (
            {
                "train/a.png": png(),
                "train/_annotations.coco.json": coco(["a.png"], {"a.png": [[60, 40, 30, 30]]}),
            },
            "box outside its image",
        ),
        ({"train/_annotations.coco.json": "{nope"}, "not valid JSON"),
    ],
)
def test_bad_zips_fail_with_a_report_naming_the_problem(entries, expected):
    ids, status = ingest(make_zip(entries))
    assert status["state"] == "error" and expected in status["error"]
    _, body = call("GET", f"{API}/datasets/{ids['dataset_id']}", A)
    m = json.loads(body)
    assert m["state"] == "failed" and expected in m["report"]


def test_something_that_is_not_a_zip_fails_too():
    ids, status = ingest(b"definitely not a zip")
    assert status["state"] == "error" and "not a valid zip" in status["error"]
