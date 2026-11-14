import io
import json

import pytest
from fakes import FakeS3, FakeValkey
from fastapi.testclient import TestClient
from PIL import Image

from geopipe import registry
from geopipe.api import create_app, parse_keys

BUCKET = "imagery"


def png(size=(64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (120, 80, 40)).save(out, "PNG")
    return out.getvalue()


def make_dataset(root, splits):
    """splits: {split: [(file_name, [[x, y, w, h], ...]), ...]}, written as a COCO folder."""
    for split, files in splits.items():
        (root / split).mkdir(parents=True)
        images, anns = [], []
        for n, (name, boxes) in enumerate(files, start=1):
            (root / split / name).write_bytes(png())
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
        coco = {
            "images": images,
            "annotations": anns,
            "categories": [{"id": 1, "name": "building"}],
        }
        (root / split / "_annotations.coco.json").write_text(json.dumps(coco))


@pytest.fixture
def world(tmp_path):
    s3, r = FakeS3(), FakeValkey()
    make_dataset(
        tmp_path,
        {
            "train": [("a.png", [[1, 2, 10, 10]]), ("b.png", [])],
            "test": [("c.png", [[5, 5, 20, 20], [30, 5, 8, 8]])],
        },
    )
    registry.register_dataset(
        s3,
        r,
        BUCKET,
        tmp_path,
        "demo",
        name="Demo",
        licence="CC BY-SA 4.0",
        attribution="made up",
        now=lambda: 1.0,
    )
    return s3, r, tmp_path


def api(s3, r):
    keys = parse_keys("ka:acme,kb:globex")
    return TestClient(create_app(r, s3, s3, BUCKET, keys))


A, B = {"X-API-Key": "ka"}, {"X-API-Key": "kb"}


def test_registering_writes_images_thumbnails_annotations_and_the_manifest_last(world):
    s3, r, _ = world
    base = "shared/datasets/demo"
    for key in (
        "manifest.json",
        "train/a.png",
        "thumbs/train/a.jpg",
        "thumbs/test/c.jpg",
        "test/_annotations.coco.json",
    ):
        assert (BUCKET, f"{base}/{key}") in s3.objects, key
    m = registry.DatasetManifest.model_validate_json(s3.objects[(BUCKET, f"{base}/manifest.json")])
    assert m.state == "ready" and m.splits == {"train": 2, "test": 1} and m.boxes == 3
    assert m.classes == ["building"] and r.zrange("datasets:shared", 0, -1) == ["demo"]


def test_registering_twice_does_not_duplicate_the_listing(world):
    s3, r, root = world
    registry.register_dataset(
        s3, r, BUCKET, root, "demo", name="Demo", licence="x", attribution="y"
    )
    assert r.zrange("datasets:shared", 0, -1) == ["demo"]


def test_a_manifest_needs_a_licence(world):
    s3, r, root = world
    with pytest.raises(ValueError, match="licence"):
        registry.register_dataset(
            s3, r, BUCKET, root, "nolicence", name="X", licence="", attribution="y"
        )


def test_ids_that_could_escape_the_prefix_are_refused(world):
    s3, r, root = world
    with pytest.raises(ValueError):
        registry.register_dataset(
            s3, r, BUCKET, root, "../x", name="X", licence="l", attribution="a"
        )
    assert registry.find(s3, BUCKET, "acme", "../shared") is None


def test_tenants_see_shared_datasets_and_only_their_own(world):
    s3, r, root = world
    registry.register_dataset(
        s3, r, BUCKET, root, "mine", scope="acme", name="Mine", licence="l", attribution="a"
    )
    c = api(s3, r)
    assert [d["id"] for d in c.get("/datasets", headers=A).json()] == ["demo", "mine"]
    assert [d["id"] for d in c.get("/datasets", headers=B).json()] == ["demo"]
    assert {d["id"]: d["scope"] for d in c.get("/datasets", headers=A).json()} == {
        "demo": "shared",
        "mine": "own",
    }
    assert c.get("/datasets/mine", headers=B).status_code == 404
    assert c.get("/datasets/mine/images/train", headers=B).status_code == 404
    assert c.get("/datasets/demo", headers=B).json()["licence"] == "CC BY-SA 4.0"


def test_an_unfinished_dataset_is_hidden_from_other_tenants_and_shown_to_its_owner(world):
    s3, r, _ = world
    m = registry.DatasetManifest(
        id="half",
        name="Half",
        classes=["building"],
        splits={},
        boxes=0,
        licence="l",
        attribution="a",
        state="failed",
        created_at=2.0,
    )
    s3.put_object(BUCKET, "tenants/acme/datasets/half/manifest.json", m.model_dump_json())
    r.zadd("tenant:acme:datasets", {"half": 2.0})
    c = api(s3, r)
    assert "half" in [d["id"] for d in c.get("/datasets", headers=A).json()]
    assert c.get("/datasets/half", headers=B).status_code == 404


def test_images_come_paged_with_box_counts_and_presigned_thumbnails(world):
    c = api(*world[:2])
    page = c.get("/datasets/demo/images/train", headers=A).json()
    assert [(i["file"], i["boxes"]) for i in page] == [("a.png", 1), ("b.png", 0)]
    assert page[0]["thumb_url"].startswith(
        "http://store/imagery/shared/datasets/demo/thumbs/train/a.jpg"
    )
    assert [
        i["file"] for i in c.get("/datasets/demo/images/train?offset=1&limit=1", headers=A).json()
    ] == ["b.png"]
    assert c.get("/datasets/demo/images/valid", headers=A).status_code == 404


def test_one_image_returns_its_ground_truth_boxes(world):
    c = api(*world[:2])
    got = c.get("/datasets/demo/images/test/c.png", headers=A).json()
    assert got["boxes"] == [[5, 5, 20, 20], [30, 5, 8, 8]] and got["width"] == 64
    assert got["url"].startswith("http://store/imagery/shared/datasets/demo/test/c.png")
    for bad in ("nope.png", "..%2Fmanifest.json", ".hidden"):
        assert c.get(f"/datasets/demo/images/test/{bad}", headers=A).status_code == 404


def test_the_shared_namespace_cannot_be_a_tenant():
    with pytest.raises(ValueError, match="built-in"):
        parse_keys("k:shared")


def test_requests_need_a_key(world):
    assert api(*world[:2]).get("/datasets").status_code == 401


def feature(box, score):
    x1, y1, x2, y2 = box
    return {
        "properties": {"class_name": "building", "pixel_bbox": [x1, y1, x2, y2], "score": score}
    }


def test_a_saved_run_rescores_to_its_saved_metrics():
    from geopipe import bench

    gt = {
        "images": [
            {"id": 1, "file_name": "a.png", "width": 100, "height": 100},
            {"id": 2, "file_name": "b.png", "width": 100, "height": 100},
        ],
        "annotations": [
            {
                "id": 1,
                "image_id": 1,
                "category_id": 1,
                "bbox": [10, 10, 20, 20],
                "iscrowd": 0,
                "area": 400,
            },
            {
                "id": 2,
                "image_id": 1,
                "category_id": 1,
                "bbox": [60, 60, 20, 20],
                "iscrowd": 0,
                "area": 400,
            },
            {
                "id": 3,
                "image_id": 2,
                "category_id": 1,
                "bbox": [5, 5, 30, 30],
                "iscrowd": 0,
                "area": 900,
            },
        ],
        "categories": [{"id": 1, "name": "building"}],
    }
    runs = {
        1: {
            "features": [feature([10, 10, 30, 30], 0.9), feature([0, 70, 15, 90], 0.6)],
            "latency": 0.2,
            "detect_seconds": 0.1,
        },
        2: {"features": [feature([6, 6, 34, 36], 0.8)], "latency": 0.3, "detect_seconds": 0.2},
    }
    scored = bench.score_model(gt, {"building": 1}, runs, [1, 2])
    dets = [
        d for i in (1, 2) for d in bench.to_coco_detections(i, runs[i]["features"], {"building": 1})
    ]
    s3, r = FakeS3(), FakeValkey()
    run_id = bench.save_run(r, s3, "yolox-tiny-buildings", "demo", "test", scored, dets, 0.05, 0.65)

    base = f"shared/runs/{run_id}"
    saved = json.loads(s3.objects[(bench.BUCKET, f"{base}/results.json")])
    again = bench.evaluate(
        gt, json.loads(s3.objects[(bench.BUCKET, f"{base}/predictions.json")]), [1, 2]
    )
    assert again["map50"] == pytest.approx(saved["map50"]) and saved["map50"] > 0
    assert again["pr_precision"] == saved["pr_precision"] and len(saved["pr_recall"]) == 101
    manifest = registry.RunManifest.model_validate_json(
        s3.objects[(bench.BUCKET, f"{base}/manifest.json")]
    )
    assert manifest.scorer == "pipeline" and manifest.metrics["map50"] == saved["map50"]
    assert manifest.metrics["detections"] == 3 and r.zrange("runs:shared", 0, -1) == [run_id]


def test_builtin_models_are_registered_and_all_name_real_detector_backends():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parents[2] / "stages" / "detector"))
    from backends import BACKENDS

    s3, r = FakeS3(), FakeValkey()
    ids = registry.register_builtin_models(s3, r, BUCKET, "spacenet-khartoum", now=lambda: 5.0)
    assert len(ids) == 6 and r.zrange("models:shared", 0, -1) == ids
    for model_id in ids:
        m = registry.ModelManifest.model_validate_json(
            s3.objects[(BUCKET, f"shared/models/{model_id}/manifest.json")]
        )
        assert m.backend in BACKENDS and m.trained_on == "spacenet-khartoum"
        assert m.classes == ["building"] and m.licence
