"""YOLOX (Apache-2.0) through ONNX Runtime: no torch and no `yolox` package at inference.

Pre- and post-processing follow the reference demo in the YOLOX repository: letterbox to
640x640 with grey padding, BGR input in 0-255, grid decode of the raw head, then
class-wise NMS.
"""

import os

import numpy as np
from backends import resolve_device
from PIL import Image

from geopipe.merge import nms
from geopipe.messages import Box

SIZE = (640, 640)
STRIDES = (8, 16, 32)
NMS_IOU = 0.65  # the value YOLOX itself uses for COCO evaluation
COCO80 = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
]


def letterbox(img: np.ndarray, size=SIZE) -> tuple[np.ndarray, float]:
    """RGB uint8 (h, w, 3) -> (1, 3, H, W) float32 BGR padded with 114, and the scale used."""
    h, w = img.shape[:2]
    ratio = min(size[0] / h, size[1] / w)
    nh, nw = int(h * ratio), int(w * ratio)
    canvas = np.full((size[0], size[1], 3), 114, np.uint8)
    canvas[:nh, :nw] = np.asarray(Image.fromarray(img).resize((nw, nh), Image.BILINEAR))
    chw = canvas[:, :, ::-1].transpose(2, 0, 1)  # RGB -> BGR, HWC -> CHW
    return np.ascontiguousarray(chw, dtype=np.float32)[None], ratio


def decode(raw: np.ndarray, size=SIZE) -> tuple[np.ndarray, np.ndarray]:
    """Raw head output (1, N, 85) -> xyxy boxes in letterboxed pixels and (N, 80) scores.

    Box coordinates in the raw output are grid offsets and log sizes per stride; objectness
    and class probabilities are already sigmoid-ed.
    """
    out = raw[0]
    grids, strides = [], []
    for s in STRIDES:
        gh, gw = size[0] // s, size[1] // s
        xv, yv = np.meshgrid(np.arange(gw), np.arange(gh))
        grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
        strides.append(np.full((gh * gw, 1), s))
    grid, stride = np.concatenate(grids), np.concatenate(strides)
    xy = (out[:, :2] + grid) * stride
    wh = np.exp(out[:, 2:4]) * stride
    return np.concatenate([xy - wh / 2, xy + wh / 2], axis=1), out[:, 4:5] * out[:, 5:]


def yolox_boxes(raw: np.ndarray, ratio: float, shape: tuple[int, int], conf: float,
                nms_iou: float = NMS_IOU) -> list[Box]:
    """Decode, threshold on the best class, undo the letterbox scale, clip, and NMS per class."""
    boxes, scores = decode(raw)
    cls = scores.argmax(1)
    best = scores[np.arange(len(scores)), cls]
    keep = best >= conf
    boxes, cls, best = boxes[keep] / ratio, cls[keep], best[keep]
    h, w = shape
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
    result = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        for k in nms(boxes[idx], best[idx], nms_iou):
            x1, y1, x2, y2 = boxes[idx[k]]
            result.append(Box(x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2),
                              score=float(best[idx[k]]), class_id=int(c), class_name=COCO80[c]))
    return result


def load_yolox(device: str = "cpu"):
    import onnxruntime as ort  # only when this backend is enabled

    if device == "cuda":
        ort.preload_dlls()  # finds the CUDA libraries that the torch wheels ship
    providers = ["CPUExecutionProvider"]
    if resolve_device(device, "CUDAExecutionProvider" in ort.get_available_providers()) == "cuda":
        providers.insert(0, "CUDAExecutionProvider")
    session = ort.InferenceSession(os.environ.get("YOLOX_ONNX", "/models/yolox_s.onnx"),
                                   providers=providers)
    if device == "cuda" and session.get_providers()[0] != "CUDAExecutionProvider":
        raise SystemExit("onnxruntime could not load its CUDA provider (missing CUDA libraries?)")
    name = session.get_inputs()[0].name

    def detect(img: np.ndarray, conf: float) -> list[Box]:
        x, ratio = letterbox(img)
        return yolox_boxes(session.run(None, {name: x})[0], ratio, img.shape[:2], conf)

    return detect
