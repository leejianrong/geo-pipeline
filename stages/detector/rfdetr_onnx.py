"""RF-DETR (Apache-2.0) through ONNX Runtime: no torch at inference.

`scripts/export_rfdetr_onnx.py` writes the model; this reproduces what the `rfdetr` package
does around it: square resize, ImageNet normalisation, then top-300 over query x class
sigmoid scores, with boxes given as normalised centre/size.
"""

import numpy as np
from backends import onnx_session
from yolox_onnx import COCO80

from geopipe.messages import Box

MEAN = np.array([0.485, 0.456, 0.406], np.float32)[:, None, None]
STD = np.array([0.229, 0.224, 0.225], np.float32)[:, None, None]
NUM_SELECT = 300
# COCO category ids that RF-DETR's 91-way head uses, in the order of the 80 class names.
COCO_IDS = [*range(1, 12), *range(13, 26), 27, 28, *range(31, 45), *range(46, 66), 67, 70,
            *range(72, 83), *range(84, 91)]
COCO_NAMES = dict(zip(COCO_IDS, COCO80, strict=True))


def _bilinear(img: np.ndarray, size: int) -> np.ndarray:
    """Plain bilinear resize to size x size, without antialiasing, like torch's default.

    PIL antialiases when it shrinks, which changes the pixels the model sees (RF-DETR Small
    lost about one mAP point on SpaceNet with it).
    """
    h, w = img.shape[:2]

    def axis(n: int):
        src = np.clip((np.arange(size) + 0.5) * n / size - 0.5, 0, n - 1)
        lo = np.floor(src).astype(int)
        return lo, np.minimum(lo + 1, n - 1), (src - lo).astype(np.float32)

    y0, y1, fy = axis(h)
    x0, x1, fx = axis(w)
    top, bot = img[y0], img[y1]  # gather in uint8: four times less memory traffic than float
    fx, fy = fx[None, :, None], fy[:, None, None]
    upper = top[:, x0] * (1 - fx) + top[:, x1] * fx
    lower = bot[:, x0] * (1 - fx) + bot[:, x1] * fx
    return upper * (1 - fy) + lower * fy


def preprocess(img: np.ndarray, size: int) -> np.ndarray:
    return ((_bilinear(img, size).transpose(2, 0, 1) / 255.0 - MEAN) / STD)[None].astype(
        np.float32)


def rfdetr_boxes(boxes: np.ndarray, logits: np.ndarray, shape: tuple[int, int], conf: float,
                 names: dict[int, str]) -> list[Box]:
    """Raw outputs (1, Q, 4) cx,cy,w,h in 0-1 and (1, Q, C) logits -> boxes in image pixels."""
    h, w = shape
    prob = 1.0 / (1.0 + np.exp(-logits[0]))
    top = np.argsort(-prob.ravel())[:NUM_SELECT]
    query, label = np.divmod(top, prob.shape[1])
    score = prob.ravel()[top]
    result = []
    for q, c, sc in zip(query, label, score, strict=True):
        if sc <= conf or int(c) not in names:
            continue
        cx, cy, bw, bh = boxes[0, q]
        result.append(Box(
            x1=float(np.clip((cx - bw / 2) * w, 0, w)), y1=float(np.clip((cy - bh / 2) * h, 0, h)),
            x2=float(np.clip((cx + bw / 2) * w, 0, w)), y2=float(np.clip((cy + bh / 2) * h, 0, h)),
            score=float(sc), class_id=int(c), class_name=names[int(c)]))
    return result


def load_rfdetr_onnx(device: str, path: str, names: dict[int, str]):
    session = onnx_session(device, path)
    inp = session.get_inputs()[0]
    size = inp.shape[2]  # square, static

    def detect(img: np.ndarray, conf: float) -> list[Box]:
        boxes, logits = session.run(None, {inp.name: preprocess(img, size)})
        return rfdetr_boxes(boxes, logits, img.shape[:2], conf, names)

    return detect
