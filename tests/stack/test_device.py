"""CPU vs GPU: tiles/s and detection parity. Run `make bench-device` (needs a GPU)."""

import json
import os
import time

import numpy as np
import pytest
from conftest import run_job, upload_photo_tif
from PIL import Image

from geopipe.bench import write_geotiff

TAG = os.environ.get("DEVICE_TAG")
OUT = "/data/out"
MODELS = ("rfdetr", "yolox")


def iou(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(w, 0) * max(h, 0)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def matched(feats, other, min_iou=0.9, score_tol=0.05) -> int:
    """Features that have a same-class partner in `other` at IoU >= min_iou, scores within tol."""
    free = list(other)
    n = 0
    for f in sorted(feats, key=lambda f: -f["score"]):
        for g in free:
            if (g["class_name"] == f["class_name"] and abs(g["score"] - f["score"]) <= score_tol
                    and iou(f["pixel_bbox"], g["pixel_bbox"]) >= min_iou):
                free.remove(g)
                n += 1
                break
    return n


@pytest.mark.slow
@pytest.mark.skipif(not TAG, reason="set DEVICE_TAG (cpu or gpu): see make bench-device")
@pytest.mark.parametrize("model", MODELS)
def test_throughput_and_parity(stack, tmp_path, model):
    r, s3, _ = stack
    key = upload_photo_tif(s3)  # also leaves the photo in /data/cache
    photo = np.array(Image.open("/data/cache/cats.jpg").convert("RGB"))
    mosaic = tmp_path / "mosaic.tif"
    write_geotiff(np.tile(photo, (6, 6, 1)), mosaic)  # 3840 x 2880: real objects on every tile
    s3.upload_file(str(mosaic), "imagery", "mosaic.tif")

    run_job(r, s3, key, model=model, conf=0.3)  # warm-up: model load and CUDA init
    started = time.monotonic()
    status, fc = run_job(r, s3, "mosaic.tif", timeout=900, model=model, conf=0.31)
    wall = time.monotonic() - started
    res = {"tiles": int(status["total"]), "wall_seconds": wall,
           "tiles_per_s": int(status["total"]) / wall,
           "detect_ms_per_tile":
               1000 * float(status["detect_seconds"]) / int(status["tiles_detected"]),
           "features": [f["properties"] for f in fc["features"]]}
    assert res["features"]
    print(f"\n{TAG} {model}: {res['tiles']} tiles, {res['tiles_per_s']:.1f} tiles/s, "
          f"{res['detect_ms_per_tile']:.0f} ms/tile in the detector")
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/device-{TAG}-{model}.json", "w") as f:
        json.dump(res, f)

    other = f"{OUT}/device-{'gpu' if TAG == 'cpu' else 'cpu'}-{model}.json"
    if os.path.exists(other):
        theirs = json.load(open(other))["features"]
        both = matched(res["features"], theirs)
        print(f"parity: {both} of {len(res['features'])} / {len(theirs)} features match")
        assert both >= 0.95 * max(len(theirs), len(res["features"]))
