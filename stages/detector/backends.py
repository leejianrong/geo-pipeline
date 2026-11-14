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


def onnx_session(device: str, path: str):
    """An ONNX Runtime session on the requested device with `DETECTOR_THREADS` CPU threads."""
    import onnxruntime as ort  # only when an ONNX backend is enabled

    if device == "cuda":
        ort.preload_dlls()  # finds the CUDA libraries that the torch wheels ship
    providers = ["CPUExecutionProvider"]
    if resolve_device(device, "CUDAExecutionProvider" in ort.get_available_providers()) == "cuda":
        providers.insert(0, "CUDAExecutionProvider")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = thread_count()
    session = ort.InferenceSession(path, opts, providers=providers)
    if device == "cuda" and session.get_providers()[0] != "CUDAExecutionProvider":
        raise SystemExit("onnxruntime could not load its CUDA provider (missing CUDA libraries?)")
    return session


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


def load_rfdetr(device: str, size: str = "Small", run: str | None = None) -> Backend:
    """RF-DETR Small or Nano; `run` selects a SpaceNet fine-tune under /weights.

    With DETECTOR_RUNTIME=onnx (the slim image) the ONNX export runs on ONNX Runtime and torch
    is not imported; otherwise the `rfdetr` package runs it.
    """
    if os.environ.get("DETECTOR_RUNTIME") == "onnx":
        from rfdetr_onnx import COCO_NAMES, load_rfdetr_onnx

        file = f"rfdetr-{size.lower()}.onnx"
        return load_rfdetr_onnx(device, f"/weights/{run}/{file}" if run else f"/models/{file}",
                                {0: "building"} if run else COCO_NAMES)
    import rfdetr  # heavy import, only when this backend is enabled
    import torch

    torch.set_num_threads(thread_count())
    weights = f"/weights/{run}/checkpoint_best_total.pth" if run else None
    model = getattr(rfdetr, f"RFDETR{size}")(
        device=resolve_device(device, torch.cuda.is_available()),
        **({"pretrain_weights": weights} if weights else {}))
    return lambda img, conf: to_boxes(model.predict(img, threshold=conf))


def load_yolox_backend(device: str, variant: str = "s") -> Backend:
    from yolox_onnx import load_yolox

    return load_yolox(device, f"/models/yolox_{variant}.onnx")


def load_yolox_buildings(device: str, run: str = "yolox-spacenet", int8: bool = False) -> Backend:
    """A YOLOX fine-tuned on SpaceNet buildings (`make train-yolox`), exported to ONNX and
    optionally quantised (`make quantise`)."""
    from yolox_onnx import load_yolox

    return load_yolox(device, f"/weights/{run}/yolox_buildings{'_int8' if int8 else ''}.onnx",
                      ["building"])


# Factories taking the device, so a backend is only loaded (and its dependencies imported)
# when enabled.
BACKENDS: dict[str, Callable[[str], Backend]] = {
    "stub": lambda device: stub_detect, "rfdetr": load_rfdetr, "yolox": load_yolox_backend,
    "rfdetr-nano": lambda device: load_rfdetr(device, "Nano"),
    "yolox-tiny": lambda device: load_yolox_backend(device, "tiny"),
    "rfdetr-buildings": lambda d: load_rfdetr(d, "Small", "rfdetr-spacenet"),
    "yolox-buildings": load_yolox_buildings,
    "rfdetr-nano-buildings": lambda d: load_rfdetr(d, "Nano", "rfdetr-spacenet-nano"),
    "yolox-tiny-buildings": lambda d: load_yolox_buildings(d, "yolox-spacenet-tiny-416"),
    "yolox-buildings-int8": lambda d: load_yolox_buildings(d, int8=True),
    "yolox-tiny-buildings-int8": lambda d: load_yolox_buildings(d, "yolox-spacenet-tiny-416", True)}
