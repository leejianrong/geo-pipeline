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

    await send(session, upload, "pick", {"filename": "d.zip", "size": 10, "content_type": ""})
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


def sample_zip(good: bool) -> bytes:
    import io
    import json
    import zipfile

    from PIL import Image

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        png = io.BytesIO()
        Image.new("RGB", (64, 48), (90, 120, 60)).save(png, "PNG")
        names = ["a.png"] if good else ["a.png", "gone.png"]
        zf.writestr("train/a.png", png.getvalue())
        images = [
            {"id": n, "file_name": f, "width": 64, "height": 48}
            for n, f in enumerate(names, start=1)
        ]
        anns = [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [1, 1, 9, 9], "iscrowd": 0}]
        zf.writestr(
            "train/_annotations.coco.json",
            json.dumps(
                {"images": images, "annotations": anns, "categories": [{"id": 1, "name": "roof"}]}
            ),
        )
    return buf.getvalue()


async def put_zip(session, upload, name, data):
    await send(session, upload, "pick", {"filename": name, "size": len(data), "content_type": ""})
    url = value(upload, "target")["url"]
    req = urllib.request.Request(
        url.replace("localhost:8333", "seaweedfs:8333"),
        data=data,
        method="PUT",
        headers={"Host": "localhost:8333"},
    )
    assert urllib.request.urlopen(req, timeout=30).status == 200
    await send(session, upload, "done", {"filename": name, "size": len(data), "ok": True})


def texts(session):
    return [
        str(value(t, "blocks") if "blocks" in t.reactive_props() else value(t, "text"))
        for t in session._by_id.values()
        if t.type == "text"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("good", [True, False])
async def test_the_form_creates_a_dataset_or_shows_why_not(good):
    import geopipe.bus as bus

    session = new_session(f"train-form-{good}")
    await sign_in(session, "dev-key-a")
    upload = labelled(session, "directupload", "Dataset zip")
    button = labelled(session, "button", "Create dataset")

    await send(session, button, "click")  # nothing uploaded yet
    assert any("Upload a zip first" in t for t in texts(session))

    await put_zip(session, upload, "d.zip", sample_zip(good))
    await send(session, button, "click")  # no name or licence yet
    assert any("needs a name and a licence" in t for t in texts(session))

    for label, text in (
        ("Dataset name", "Form Roofs"),
        ("Dataset licence", "CC BY 4.0"),
        ("Dataset attribution", "me"),
    ):
        await send(session, labelled(session, "textinput", label), "input", {"value": text})
    await send(session, button, "click")  # polls the trainer until it answers
    shown = "\n".join(texts(session))
    if good:
        assert "Ready: 1 images, 1 boxes" in shown
        assert "Form Roofs" in shown and ": ready, 1 images, 1 boxes" in shown
    else:
        assert "was rejected" in shown and "train/gone.png" in shown
        assert "Form Roofs" in shown and ": failed" in shown
    bus.connect().delete("tenant:acme:datasets")
