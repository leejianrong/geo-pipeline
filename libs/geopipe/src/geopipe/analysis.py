"""Numbers behind the plots. No matplotlib or GDAL, so these run in plain unit tests."""

import math
from collections import Counter

import numpy as np

from .messages import Window


def distributions(features: list[dict]) -> dict:
    """Scores, box sizes (sqrt of area, in source pixels) and per-class counts."""
    props = [f["properties"] for f in features]
    sizes = [math.sqrt(max(0.0, (p["pixel_bbox"][2] - p["pixel_bbox"][0])
                          * (p["pixel_bbox"][3] - p["pixel_bbox"][1]))) for p in props]
    return {"scores": [p["score"] for p in props], "sizes": sizes,
            "classes": dict(Counter(p["class_name"] for p in props))}


def tile_counts(windows: list[Window], features: list[dict]) -> list[int]:
    """Detections per window, assigning each box to every window that contains its centre."""
    if not features or not windows:
        return [0] * len(windows)
    boxes = np.array([f["properties"]["pixel_bbox"] for f in features], dtype=float)
    cx, cy = (boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2
    x0 = np.array([w.x_off for w in windows])
    y0 = np.array([w.y_off for w in windows])
    x1 = x0 + np.array([w.w for w in windows])
    y1 = y0 + np.array([w.h for w in windows])
    counts = np.zeros(len(windows), dtype=int)
    for s in range(0, len(cx), 2000):  # chunked so a dense job does not build a huge matrix
        px, py = cx[s:s + 2000, None], cy[s:s + 2000, None]
        counts += ((x0 <= px) & (px < x1) & (y0 <= py) & (py < y1)).sum(axis=0)
    return counts.tolist()


def timings(status: dict, meta: dict) -> dict:
    """Stage seconds, wall time (includes queue wait) and throughput for one finished job."""
    def num(key):
        return float(status.get(key) or 0)

    wall = num("done_at") - num("submitted_at") if status.get("submitted_at") else None
    tiles = int(status.get("total") or 0)
    mpix = meta["width"] * meta["height"] / 1e6
    return {"slice": num("slice_seconds"), "detect": num("detect_seconds"),
            "finalise": num("finalise_seconds"), "wall": wall, "tiles": tiles,
            "tiles_per_s": tiles / wall if wall else None,
            "mpix_per_s": mpix / wall if wall else None,
            "boxes_raw": int(num("boxes_raw")), "boxes_final": int(num("boxes_final"))}
