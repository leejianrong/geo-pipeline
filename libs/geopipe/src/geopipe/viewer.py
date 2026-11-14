"""What the web UI does with a job's GeoJSON: filter detections and place them on the preview."""


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
