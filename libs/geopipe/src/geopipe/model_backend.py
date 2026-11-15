"""An ONNX Runtime backend for a registered model: `detect(img_rgb_uint8, conf) -> list[Box]`."""

from collections.abc import Callable

import numpy as np

from . import registry
from .messages import Box

Backend = Callable[[np.ndarray, float], list[Box]]


def build_backend(device: str, model: registry.ModelManifest, path: str) -> Backend:
    """An ONNX Runtime backend for a registered model, by the decoder its manifest names."""
    if model.decoder == "yolox":
        from .yolox_onnx import load_yolox

        return load_yolox(device, path, model.classes)
    from .rfdetr_onnx import load_rfdetr_onnx

    return load_rfdetr_onnx(device, path, dict(enumerate(model.classes)))
