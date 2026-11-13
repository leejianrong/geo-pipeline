"""Merge per-tile boxes into one set in raster pixel coordinates."""

import numpy as np

EDGE_TOL = 1.0  # px


def nms(xyxy: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
    """Greedy non-maximum suppression; returns indices to keep, best score first."""
    order = np.argsort(-scores)
    areas = (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])
    keep = []
    while order.size:
        i, rest = order[0], order[1:]
        keep.append(int(i))
        w = np.minimum(xyxy[i, 2], xyxy[rest, 2]) - np.maximum(xyxy[i, 0], xyxy[rest, 0])
        h = np.minimum(xyxy[i, 3], xyxy[rest, 3]) - np.maximum(xyxy[i, 1], xyxy[rest, 1])
        w, h = np.clip(w, 0, None), np.clip(h, 0, None)
        inter = w * h
        order = rest[inter / (areas[i] + areas[rest] - inter) <= iou_thr]
    return keep


def _cut_by_interior_edge(b: dict, win: dict, width: int, height: int) -> bool:
    """True if the box touches a tile edge that is not the raster border: another tile
    sees that object whole (given overlap), so the clipped copy is dropped."""
    return (
        (b["x1"] <= EDGE_TOL and win["x_off"] > 0)
        or (b["x2"] >= win["w"] - EDGE_TOL and win["x_off"] + win["w"] < width)
        or (b["y1"] <= EDGE_TOL and win["y_off"] > 0)
        or (b["y2"] >= win["h"] - EDGE_TOL and win["y_off"] + win["h"] < height)
    )


def merge_boxes(per_tile: dict[int, dict], width: int, height: int, overlap: float,
                iou: float) -> list[dict]:
    """per_tile: idx -> {"window": {...}, "boxes": [{x1,y1,x2,y2,score,class_id,class_name}]}.
    Returns boxes in raster pixels. With overlap > 0, boxes cut by an interior tile edge are
    dropped, then class-wise NMS removes duplicates seen by several tiles."""
    boxes = []
    for idx in sorted(per_tile):
        win = per_tile[idx]["window"]
        for b in per_tile[idx]["boxes"]:
            if overlap > 0 and _cut_by_interior_edge(b, win, width, height):
                continue
            boxes.append({**b, "x1": b["x1"] + win["x_off"], "x2": b["x2"] + win["x_off"],
                          "y1": b["y1"] + win["y_off"], "y2": b["y2"] + win["y_off"]})
    kept = []
    for cid in {b["class_id"] for b in boxes}:
        group = [b for b in boxes if b["class_id"] == cid]
        xyxy = np.array([[b["x1"], b["y1"], b["x2"], b["y2"]] for b in group])
        kept += [group[i] for i in nms(xyxy, np.array([b["score"] for b in group]), iou)]
    return sorted(kept, key=lambda b: (b["y1"], b["x1"]))
