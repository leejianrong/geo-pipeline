"""Detector backends. A backend is `detect(img_rgb_uint8, conf) -> list[Box]` in tile pixels."""

from collections.abc import Callable

import numpy as np

from geopipe.messages import Box

Backend = Callable[[np.ndarray, float], list[Box]]


def stub_detect(img: np.ndarray, conf: float) -> list[Box]:
    """Test backend: one box around all bright pixels (assumes at most one object per tile)."""
    ys, xs = np.where(img.max(axis=2) > 128)
    if xs.size == 0:
        return []
    return [Box(x1=float(xs.min()), y1=float(ys.min()), x2=float(xs.max() + 1),
                y2=float(ys.max() + 1), score=1.0, class_id=0, class_name="bright")]


def to_boxes(det) -> list[Box]:
    """Normalise an RF-DETR `supervision.Detections` into Box models."""
    if len(det.xyxy) == 0:
        return []
    return [Box(x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2), score=float(score),
                class_id=int(cid), class_name=str(name))
            for (x1, y1, x2, y2), score, cid, name
            in zip(det.xyxy, det.confidence, det.class_id, det.data["class_name"], strict=True)]


def load_rfdetr() -> Backend:
    from rfdetr import RFDETRSmall  # heavy import, only when this backend is enabled

    model = RFDETRSmall()
    return lambda img, conf: to_boxes(model.predict(img, threshold=conf))


def load_yolox_backend() -> Backend:
    from yolox_onnx import load_yolox

    return load_yolox()


# Factories, so a backend is only loaded (and its dependencies imported) when enabled.
BACKENDS: dict[str, Callable[[], Backend]] = {
    "stub": lambda: stub_detect, "rfdetr": load_rfdetr, "yolox": load_yolox_backend}
