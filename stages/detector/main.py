"""Detector: read a tile PNG, run the configured backend, emit boxes in tile pixel coordinates."""

import io
import logging
import socket

import numpy as np
from PIL import Image

from geopipe import bus, store
from geopipe.messages import DETECTIONS, TILES, Box, Detection, Tile


def stub_detect(img: np.ndarray) -> list[Box]:
    """Test backend: one box around all bright pixels (assumes at most one object per tile)."""
    ys, xs = np.where(img.max(axis=2) > 128)
    if xs.size == 0:
        return []
    return [Box(x1=float(xs.min()), y1=float(ys.min()), x2=float(xs.max() + 1),
                y2=float(ys.max() + 1), score=1.0, class_id=0, class_name="bright")]


def detect_tile(r, s3, tile: Tile) -> None:
    body = s3.get_object(Bucket=tile.tile_bucket, Key=tile.tile_key)["Body"].read()
    img = np.array(Image.open(io.BytesIO(body)).convert("RGB"))
    # Count before publishing so the status is complete by the time postprocess says done.
    r.hincrby(f"job:{tile.job_id}:status", "tiles_detected", 1)
    bus.publish(r, DETECTIONS, Detection(tenant_id=tile.tenant_id, job_id=tile.job_id,
                                         idx=tile.idx, total=tile.total, window=tile.window,
                                         boxes=stub_detect(img)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r, s3 = bus.connect(), store.client()
    bus.consume(r, TILES, "detector", socket.gethostname(), Tile, lambda t: detect_tile(r, s3, t))
