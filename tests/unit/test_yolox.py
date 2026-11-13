import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "stages" / "detector"))
from yolox_onnx import COCO80, letterbox, yolox_boxes  # noqa: E402

FIXTURES = Path(__file__).parents[1] / "fixtures"


def raw_output(*anchors):
    """A (1, 8400, 85) head output that is empty except for the given anchors.

    Each anchor is (index, dx, dy, log_w, log_h, objectness, class_id, class_prob).
    """
    out = np.zeros((1, 8400, 85), np.float32)
    for idx, dx, dy, lw, lh, obj, cid, prob in anchors:
        out[0, idx, :4] = (dx, dy, lw, lh)
        out[0, idx, 4] = obj
        out[0, idx, 5 + cid] = prob
    return out


def test_letterbox_keeps_aspect_ratio_pads_with_grey_and_flips_to_bgr():
    red = np.zeros((480, 640, 3), np.uint8)
    red[:, :, 0] = 255  # RGB red
    x, ratio = letterbox(red)
    assert x.shape == (1, 3, 640, 640) and x.dtype == np.float32 and ratio == 1.0
    assert x[0, 2, 100, 100] == 255 and x[0, 0, 100, 100] == 0  # red ends up in the R plane of BGR
    assert x[0, 0, 600, 100] == 114  # below the 480-row image: padding
    _, ratio = letterbox(np.zeros((1280, 640, 3), np.uint8))
    assert ratio == 0.5


def test_decode_places_a_box_from_the_stride_8_grid():
    # anchor 1610 is cell (x=10, y=20) at stride 8; offset +0.5 -> centre (84, 164); size 5*8=40
    raw = raw_output((20 * 80 + 10, 0.5, 0.5, np.log(5), np.log(5), 0.9, 15, 0.8))
    [box] = yolox_boxes(raw, ratio=1.0, shape=(640, 640), conf=0.25)
    assert (box.x1, box.y1, box.x2, box.y2) == pytest.approx((64.0, 144.0, 104.0, 184.0), abs=1e-3)
    assert box.class_id == 15 and box.class_name == "cat"
    assert abs(box.score - 0.72) < 1e-6


def test_boxes_are_scaled_back_to_the_original_image_and_clipped():
    raw = raw_output((0, 0.0, 0.0, np.log(5), np.log(5), 0.9, 0, 0.9))  # centre (0,0), 40x40
    [box] = yolox_boxes(raw, ratio=0.5, shape=(300, 300), conf=0.25)
    assert (box.x1, box.y1) == (0.0, 0.0)  # clipped at the image edge
    assert (box.x2, box.y2) == pytest.approx((40.0, 40.0), abs=1e-3)  # 20 letterboxed px / 0.5


def test_nms_removes_same_class_duplicates_but_keeps_other_classes_and_applies_the_threshold():
    size = np.log(5)  # every box below is 40x40 centred at (84, 164)
    row = 20 * 80
    raw = raw_output(
        (row + 10, 0.5, 0.5, size, size, 0.9, 15, 0.9),  # best cat, score 0.81
        (row + 11, -0.5, 0.5, size, size, 0.8, 15, 0.8),  # same box, lower score: suppressed
        (row + 12, -1.5, 0.5, size, size, 0.9, 16, 0.7),  # same place, other class: kept
        (60 * 80 + 60, 0.5, 0.5, size, size, 0.1, 2, 0.5))  # score 0.05, below conf
    boxes = yolox_boxes(raw, ratio=1.0, shape=(640, 640), conf=0.25)
    found = sorted((b.class_name, round(b.score, 2)) for b in boxes)
    assert found == [("cat", 0.81), ("dog", 0.63)]


def test_class_names_match_cocos_80_categories_in_order():
    cats = json.loads((FIXTURES / "coco_mini.json").read_text())["categories"]
    assert [c["name"] for c in sorted(cats, key=lambda c: c["id"])] == COCO80
