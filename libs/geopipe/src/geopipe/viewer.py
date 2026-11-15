"""What the web UI does with data from the API: filter detections, place boxes on images, and
summarise a dataset's annotations."""


def filter_features(features: list[dict], min_score: float, class_text: str = "") -> list[dict]:
    """Features with score >= `min_score` whose class name contains `class_text` (any case)."""
    needle = class_text.strip().lower()
    return [f for f in features
            if f["properties"]["score"] >= min_score
            and needle in f["properties"]["class_name"].lower()]


def overlay_boxes(features: list[dict], width: int, height: int) -> list[dict]:
    """Detections as boxes in fractions of the image, the way the image overlay wants them."""
    boxes = []
    for f in features:
        p = f["properties"]
        x1, y1, x2, y2 = p["pixel_bbox"]
        boxes.append({"x": x1 / width, "y": y1 / height, "w": (x2 - x1) / width,
                      "h": (y2 - y1) / height, "label": f"{p['class_name']} {p['score']:.2f}",
                      "score": p["score"]})
    return boxes


def histogram(values: list[float], edges: list[float]) -> list[int]:
    """Counts per bin: [edges[0], edges[1]), ..., and a last bin from the final edge up."""
    counts = [0] * len(edges)
    for v in values:
        for i in range(len(edges) - 1, -1, -1):
            if v >= edges[i]:
                counts[i] += 1
                break
    return counts


BOX_COUNT_EDGES = [0, 1, 6, 11, 21, 51]  # boxes per image: 0, 1-5, 6-10, 11-20, 21-50, 51+
BOX_SIZE_EDGES = [0, 8, 16, 32, 64, 128]  # sqrt(area) in pixels


def edge_labels(edges: list[float]) -> list[str]:
    spans = [f"{a:g}-{b - 1:g}" if b - 1 > a else f"{a:g}"
             for a, b in zip(edges, edges[1:], strict=False)]
    return [*spans, f"{edges[-1]:g}+"]


def dataset_stats(coco: dict) -> dict:
    """Image and box counts, boxes per image and box size histograms for a COCO annotation set."""
    per_image = {i["id"]: 0 for i in coco["images"]}
    for a in coco["annotations"]:
        per_image[a["image_id"]] = per_image.get(a["image_id"], 0) + 1
    sizes = [(a["bbox"][2] * a["bbox"][3]) ** 0.5 for a in coco["annotations"]]
    return {"images": len(coco["images"]), "boxes": len(coco["annotations"]),
            "empty_images": sum(1 for n in per_image.values() if n == 0),
            "boxes_per_image": histogram(list(per_image.values()), BOX_COUNT_EDGES),
            "box_sizes": histogram(sizes, BOX_SIZE_EDGES)}


def gt_overlay(boxes: list[list[float]], width: int, height: int, label: str = "") -> list[dict]:
    """Ground-truth [x, y, w, h] pixel boxes as boxes in fractions of the image."""
    return [{"x": x / width, "y": y / height, "w": w / width, "h": h / height, "label": label,
             "score": 1.0} for x, y, w, h in boxes]


def prediction_features(preds: list[dict], class_name: str = "building") -> list[dict]:
    """A run's `{bbox: [x, y, w, h], score}` boxes as detection features, so the model view can
    use `filter_features` like the Detect tab does."""
    return [{"properties": {"class_name": class_name, "score": p["score"],
                            "pixel_bbox": [p["bbox"][0], p["bbox"][1], p["bbox"][0] + p["bbox"][2],
                                           p["bbox"][1] + p["bbox"][3]]}} for p in preds]


def _iou(a: list[float], b: list[float]) -> float:
    """IoU of two [x, y, w, h] boxes."""
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    inter = max(w, 0) * max(h, 0)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def match_image(gt: list[list[float]], features: list[dict], iou_thr: float = 0.5) -> dict:
    """Match one image's detections to its ground truth the way `bench.match_at_iou` does
    (greedy, best score first, one detection per box) and say which is which.

    Returns `tp`, `fp`, `fn` counts and `boxes`, the overlay boxes: green true positives, red
    false positives, amber ground-truth boxes nothing found."""
    taken: set[int] = set()
    boxes, tp = [], 0
    for f in sorted(features, key=lambda f: -f["properties"]["score"]):
        x1, y1, x2, y2 = f["properties"]["pixel_bbox"]
        det = [x1, y1, x2 - x1, y2 - y1]
        best, best_i = -1.0, -1
        for i, g in enumerate(gt):
            v = _iou(det, g) if i not in taken else -1.0
            if v > best:
                best, best_i = v, i
        hit = best >= iou_thr
        if hit:
            taken.add(best_i)
            tp += 1
        boxes.append((det, f["properties"]["score"], "TP" if hit else "FP"))
    boxes += [(g, 1.0, "missed") for i, g in enumerate(gt) if i not in taken]
    return {"tp": tp, "fp": len(features) - tp, "fn": len(gt) - tp, "boxes": boxes}


STATUS_COLOURS = {"TP": "#2e9d5b", "FP": "#d64545", "missed": "#e0a100"}


def match_overlay(matched: dict, width: int, height: int) -> list[dict]:
    """`match_image` boxes as overlay boxes in fractions of the image, coloured by outcome."""
    return [{"x": x / width, "y": y / height, "w": w / width, "h": h / height,
             "label": status if status == "missed" else f"{status} {score:.2f}",
             "score": score, "color": STATUS_COLOURS[status]}
            for (x, y, w, h), score, status in matched["boxes"]]


def image_outcomes(coco: dict, preds: dict[str, list[dict]], min_score: float,
                   iou_thr: float = 0.5) -> list[dict]:
    """Per image: file, ground-truth count, tp, fp, fn at `min_score`, worst first (most errors,
    then file name)."""
    gts: dict[int, list] = {}
    for a in coco["annotations"]:
        gts.setdefault(a["image_id"], []).append(a["bbox"])
    rows = []
    for info in coco["images"]:
        feats = filter_features(prediction_features(preds.get(info["file_name"], [])), min_score)
        m = match_image(gts.get(info["id"], []), feats, iou_thr)
        rows.append({"file": info["file_name"], "gt": len(gts.get(info["id"], [])),
                     "tp": m["tp"], "fp": m["fp"], "fn": m["fn"]})
    return sorted(rows, key=lambda r: (-(r["fp"] + r["fn"]), r["file"]))
