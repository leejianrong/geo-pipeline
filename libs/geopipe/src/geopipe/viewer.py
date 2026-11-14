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
