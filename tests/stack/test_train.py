"""S3: the Train tab asks the API for a presigned PUT and reports the finished upload.

The browser does the PUT itself (see scripts/dataset_upload_check.py for a real one); here the
test plays the browser: it takes the component's target and PUTs to it, then sends `done`."""

import urllib.request

import pytest
from conftest import BUCKET
from test_frontend import labelled, new_session, send, sign_in

from geopipe import store


def value(component, prop="value"):
    return component.reactive_props()[prop]()


@pytest.mark.asyncio
async def test_the_train_tab_gets_a_target_and_reports_a_finished_upload():
    session = new_session("train-a")
    await sign_in(session, "dev-key-a")
    upload = labelled(session, "directupload", "Dataset zip")
    assert value(upload, "target") is None

    await send(session, upload, "pick", {"filename": "d.zip", "size": 9, "content_type": ""})
    target = value(upload, "target")
    assert "/tenants/acme/uploads/datasets/" in target["url"]

    req = urllib.request.Request(
        target["url"].replace("localhost:8333", "seaweedfs:8333"),
        data=b"PK\x03\x04zipzip",
        method="PUT",
        headers={"Host": "localhost:8333"},
    )
    assert urllib.request.urlopen(req, timeout=30).status == 200
    key = target["url"].split("/imagery/")[1].split("?")[0]
    assert store.client().head_object(Bucket=BUCKET, Key=key)["ContentLength"] == 10

    await send(session, upload, "done", {"filename": "d.zip", "size": 10, "ok": True})
    assert value(upload, "target") is None
    texts = [
        str(value(t, "text"))
        for t in session._by_id.values()
        if t.type == "text" and "text" in t.reactive_props()
    ]
    assert any("Uploaded d.zip" in s for s in texts)
