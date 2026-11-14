"""Detector backends. A backend is `detect(img_rgb_uint8, conf) -> list[Box]` in tile pixels."""

import os
from collections.abc import Callable

import numpy as np

from geopipe.messages import Box

Backend = Callable[[np.ndarray, float], list[Box]]


def resolve_device(requested: str, cuda_available: bool) -> str:
    """`cpu` or `cuda`; asking for cuda without a GPU is an error, never a silent CPU run."""
    if requested not in ("cpu", "cuda"):
        raise SystemExit(f"DETECTOR_DEVICE must be cpu or cuda, got {requested!r}")
    if requested == "cuda" and not cuda_available:
        raise SystemExit("DETECTOR_DEVICE=cuda but no GPU is visible to this container "
                         "(use the GPU image and compose.gpu.yaml, with the NVIDIA runtime)")
    return requested


def thread_count(raw: str | None = None) -> int:
    """CPU threads per detector, from DETECTOR_THREADS (default 4: more was not faster)."""
    raw = os.environ.get("DETECTOR_THREADS", "4") if raw is None else raw
    if not raw.isdigit() or int(raw) < 1:
        raise SystemExit(f"DETECTOR_THREADS must be a positive integer, got {raw!r}")
    return int(raw)


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


def load_rfdetr(device: str, weights: str | None = None) -> Backend:
    import torch
    from rfdetr import RFDETRSmall  # heavy import, only when this backend is enabled

    torch.set_num_threads(thread_count())
    model = RFDETRSmall(device=resolve_device(device, torch.cuda.is_available()),
                        **({"pretrain_weights": weights} if weights else {}))
    return lambda img, conf: to_boxes(model.predict(img, threshold=conf))


def load_yolox_backend(device: str) -> Backend:
    from yolox_onnx import load_yolox

    return load_yolox(device)


def load_yolox_buildings(device: str) -> Backend:
    """YOLOX-s fine-tuned on SpaceNet buildings (`make train-yolox`), exported to ONNX."""
    from yolox_onnx import load_yolox

    return load_yolox(device, os.environ.get(
        "YOLOX_BUILDINGS_ONNX", "/weights/yolox-spacenet/yolox_buildings.onnx"), ["building"])


def load_rfdetr_buildings(device: str) -> Backend:
    """RF-DETR Small fine-tuned on SpaceNet buildings (`make train-rfdetr`)."""
    return load_rfdetr(device, os.environ.get(
        "RFDETR_BUILDINGS_WEIGHTS", "/weights/rfdetr-spacenet/checkpoint_best_total.pth"))


# Factories taking the device, so a backend is only loaded (and its dependencies imported)
# when enabled.
BACKENDS: dict[str, Callable[[str], Backend]] = {
    "stub": lambda device: stub_detect, "rfdetr": load_rfdetr, "yolox": load_yolox_backend,
    "rfdetr-buildings": load_rfdetr_buildings, "yolox-buildings": load_yolox_buildings}
