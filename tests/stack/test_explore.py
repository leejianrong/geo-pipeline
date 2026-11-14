"""S1: browse a registered dataset through the indah session, against the real API and store."""

import io
import json
import urllib.error
import urllib.request

import pytest
from conftest import BUCKET
from PIL import Image
from test_frontend import labelled, new_session, nodes, send, sign_in

from geopipe import bus, registry, store

BOXES = {"t1.png": [[2, 3, 20, 10], [30, 5, 10, 10], [40, 30, 8, 8]], "t2.png": []}


def write_split(root, split, files):
    (root / split).mkdir(parents=True)
    images, anns = [], []
    for n, (name, boxes) in enumerate(files.items(), start=1):
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (90, 120, 60)).save(buf, "PNG")
        (root / split / name).write_bytes(buf.getvalue())
        images.append({"id": n, "file_name": name, "width": 64, "height": 48})
        anns += [
            {
                "id": n * 100 + k,
                "image_id": n,
                "category_id": 1,
                "bbox": b,
                "area": b[2] * b[3],
                "iscrowd": 0,
            }
            for k, b in enumerate(boxes)
        ]
    coco = {"images": images, "annotations": anns, "categories": [{"id": 1, "name": "building"}]}
    (root / split / "_annotations.coco.json").write_text(json.dumps(coco))


@pytest.fixture(scope="module")
def datasets(tmp_path_factory):
    root = tmp_path_factory.mktemp("explore")
    write_split(root, "train", {"a.png": [[1, 1, 9, 9]], "b.png": [], "c.png": [[5, 5, 9, 9]]})
    write_split(root, "test", BOXES)
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    for scope, ds in ((registry.SHARED, "explore-demo"), ("acme", "acme-private")):
        registry.register_dataset(
            s3,
            r,
            BUCKET,
            root,
            ds,
            scope=scope,
            name=ds.title(),
            licence="CC BY-SA 4.0",
            attribution="Test data, made up.",
        )


def value(component, prop="value"):
    return component.reactive_props()[prop]()


def stat(session, label):
    return int(value(labelled(session, "stat", label)))


@pytest.mark.asyncio
async def test_explore_shows_images_boxes_statistics_and_attribution(datasets):
    session = new_session("explore-a")
    await sign_in(session, "dev-key-a")

    select = labelled(session, "select", "Dataset")
    assert sorted(o["value"] if isinstance(o, dict) else o for o in value(select, "options")) == [
        "acme-private",
        "explore-demo",
    ]
    assert value(select) == "acme-private" or value(select) == "explore-demo"
    await send(session, select, "change", {"value": "explore-demo"})

    assert stat(session, "Images") == 2 and stat(session, "Boxes") == 3
    assert stat(session, "Empty images") == 1
    assert [g["alt"] for g in value(nodes(session, "gallery")[0], "images")] == ["t1.png", "t2.png"]
    shown = [
        str(value(t, "blocks")) for t in nodes(session, "text") if "blocks" in t.reactive_props()
    ]
    assert any("CC BY-SA 4.0" in s and "Test data, made up." in s for s in shown)
    assert any(
        "0: 1 · 1-5: 1 · 6-10: 0" in str(value(t, "text"))
        for t in nodes(session, "text")
        if "text" in t.reactive_props()
    )  # the boxes-per-image caption

    overlay = nodes(session, "imageoverlay")[-1]  # the Explore tab's, after Detect's
    assert value(overlay, "src").startswith("data:image/png;base64,")
    assert len(value(overlay, "boxes")) == 3  # t1.png's annotation count
    assert all(0 <= b["x"] <= 1 and 0 < b["w"] <= 1 for b in value(overlay, "boxes"))
    await send(session, labelled(session, "select", "Image"), "change", {"value": "t2.png"})
    assert value(overlay, "boxes") == []

    await send(session, labelled(session, "segmented", "Split"), "change", {"value": "train"})
    assert stat(session, "Images") == 3 and stat(session, "Boxes") == 2
    assert len(value(nodes(session, "gallery")[0], "images")) == 3


@pytest.mark.asyncio
async def test_another_tenant_sees_the_shared_dataset_but_not_acmes(datasets):
    session = new_session("explore-b")
    await sign_in(session, "dev-key-b")
    options = value(labelled(session, "select", "Dataset"), "options")
    assert [o["value"] if isinstance(o, dict) else o for o in options] == ["explore-demo"]

    req = urllib.request.Request(
        "http://api:8000/datasets/acme-private", headers={"X-API-Key": "dev-key-b"}
    )
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=30)
    assert err.value.code == 404
