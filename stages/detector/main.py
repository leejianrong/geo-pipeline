"""Detector: read a tile PNG, run the backend named in the job options, emit tile-pixel boxes."""

import io
import logging
import os
import socket
import time

import numpy as np
from backends import BACKENDS, Backend
from PIL import Image
from user_models import UserModels

from geopipe import bus, store
from geopipe.messages import DETECTIONS, TILES, Detection, Tile


def pick_backend(backends: dict[str, Backend], user_models: UserModels | None, tile: Tile):
    """The backend a tile asks for: one enabled at startup, or `user:<id>` from the registry."""
    name = tile.options.model
    if name in backends:
        return backends[name]
    if name.startswith("user:") and user_models is not None:
        return user_models.get(tile.tenant_id, name.removeprefix("user:"))
    raise bus.PermanentError(f"model {name!r} is not enabled; have {sorted(backends)}")


def detect_tile(r, s3, backends: dict[str, Backend], tile: Tile,
                user_models: UserModels | None = None) -> None:
    backend = pick_backend(backends, user_models, tile)
    body = s3.get_object(Bucket=tile.tile_bucket, Key=tile.tile_key)["Body"].read()
    img = np.array(Image.open(io.BytesIO(body)).convert("RGB"))
    started = time.perf_counter()
    boxes = backend(img, tile.options.conf)
    # Count before publishing so the status is complete by the time postprocess says done.
    r.hincrby(f"job:{tile.job_id}:status", "tiles_detected", 1)
    r.hincrbyfloat(f"job:{tile.job_id}:status", "detect_seconds", time.perf_counter() - started)
    bus.publish(r, DETECTIONS, Detection(tenant_id=tile.tenant_id, job_id=tile.job_id,
                                         idx=tile.idx, total=tile.total, window=tile.window,
                                         boxes=boxes))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    default = "stub,rfdetr,yolox,yolox-tiny,rfdetr-nano"
    names = os.environ.get("DETECTOR_MODELS", default).split(",")
    unknown = [n for n in names if n not in BACKENDS]
    if unknown:
        raise SystemExit(f"unknown DETECTOR_MODELS {unknown}; available: {sorted(BACKENDS)}")
    device = os.environ.get("DETECTOR_DEVICE", "cpu")
    backends = {n: BACKENDS[n](device) for n in names}  # load everything up front: fail at startup
    r, s3 = bus.connect(), store.client()
    user_models = UserModels(s3, os.environ.get("GEOPIPE_BUCKET", "imagery"), device)
    bus.consume(r, TILES, "detector", socket.gethostname(), Tile,
                lambda t: detect_tile(r, s3, backends, t, user_models))
