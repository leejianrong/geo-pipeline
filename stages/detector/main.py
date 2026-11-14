"""Detector: read a tile PNG, run the backend named in the job options, emit tile-pixel boxes."""

import io
import logging
import os
import socket
import time

import numpy as np
from backends import BACKENDS, Backend
from PIL import Image

from geopipe import bus, store
from geopipe.messages import DETECTIONS, TILES, Detection, Tile


def detect_tile(r, s3, backends: dict[str, Backend], tile: Tile) -> None:
    if tile.options.model not in backends:
        raise bus.PermanentError(
            f"model {tile.options.model!r} is not enabled; have {sorted(backends)}")
    body = s3.get_object(Bucket=tile.tile_bucket, Key=tile.tile_key)["Body"].read()
    img = np.array(Image.open(io.BytesIO(body)).convert("RGB"))
    started = time.perf_counter()
    boxes = backends[tile.options.model](img, tile.options.conf)
    # Count before publishing so the status is complete by the time postprocess says done.
    r.hincrby(f"job:{tile.job_id}:status", "tiles_detected", 1)
    r.hincrbyfloat(f"job:{tile.job_id}:status", "detect_seconds", time.perf_counter() - started)
    bus.publish(r, DETECTIONS, Detection(tenant_id=tile.tenant_id, job_id=tile.job_id,
                                         idx=tile.idx, total=tile.total, window=tile.window,
                                         boxes=boxes))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    names = os.environ.get("DETECTOR_MODELS", "stub,rfdetr,yolox").split(",")
    unknown = [n for n in names if n not in BACKENDS]
    if unknown:
        raise SystemExit(f"unknown DETECTOR_MODELS {unknown}; available: {sorted(BACKENDS)}")
    device = os.environ.get("DETECTOR_DEVICE", "cpu")
    backends = {n: BACKENDS[n](device) for n in names}  # load everything up front: fail at startup
    r, s3 = bus.connect(), store.client()
    bus.consume(r, TILES, "detector", socket.gethostname(), Tile,
                lambda t: detect_tile(r, s3, backends, t))
