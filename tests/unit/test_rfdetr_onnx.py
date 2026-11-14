import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "stages" / "detector"))
from rfdetr_onnx import COCO_IDS, COCO_NAMES, preprocess, rfdetr_boxes  # noqa: E402


def outputs(*queries, classes=91):
    """Raw (boxes, logits) with every logit very negative except the given queries.

    Each query is (index, cx, cy, w, h, class_id, logit).
    """
    boxes = np.zeros((1, 300, 4), np.float32)
    logits = np.full((1, 300, classes), -20.0, np.float32)
    for q, cx, cy, w, h, cid, logit in queries:
        boxes[0, q] = (cx, cy, w, h)
        logits[0, q, cid] = logit
    return boxes, logits


def test_coco_ids_map_the_91_way_head_to_the_80_names():
    assert len(COCO_IDS) == 80 and COCO_NAMES[17] == "cat" and COCO_NAMES[75] == "remote"
    assert 12 not in COCO_NAMES  # unused COCO category id


def test_boxes_are_scaled_from_normalised_centre_size_to_pixels():
    boxes, logits = outputs((7, 0.5, 0.25, 0.2, 0.1, 17, 3.0))
    [b] = rfdetr_boxes(boxes, logits, shape=(400, 800), conf=0.5, names=COCO_NAMES)
    assert (b.x1, b.y1, b.x2, b.y2) == pytest.approx((320.0, 80.0, 480.0, 120.0), abs=1e-3)
    assert b.class_name == "cat" and b.score == pytest.approx(1 / (1 + np.exp(-3.0)), abs=1e-6)


def test_threshold_clipping_and_unnamed_classes():
    boxes, logits = outputs(
        (0, 0.5, 0.5, 0.2, 0.2, 17, 3.0),   # kept
        (1, 0.5, 0.5, 0.2, 0.2, 17, -1.0),  # score 0.27: below conf
        (2, 0.5, 0.5, 0.2, 0.2, 12, 3.0),   # class id with no name: dropped
        (3, 0.98, 0.5, 0.2, 0.2, 17, 3.0))  # sticks out of the image: clipped
    got = rfdetr_boxes(boxes, logits, shape=(100, 100), conf=0.5, names=COCO_NAMES)
    assert sorted(round(b.x2) for b in got) == [60, 100]


def test_fine_tuned_head_keeps_class_0_and_drops_the_no_object_slot():
    boxes, logits = outputs((0, 0.5, 0.5, 0.2, 0.2, 0, 2.0), (1, 0.2, 0.2, 0.1, 0.1, 1, 5.0),
                            classes=2)
    [b] = rfdetr_boxes(boxes, logits, shape=(100, 100), conf=0.5, names={0: "building"})
    assert b.class_name == "building" and b.class_id == 0


def test_preprocess_resizes_to_a_square_and_normalises_like_imagenet():
    x = preprocess(np.full((50, 80, 3), 255, np.uint8), 384)
    assert x.shape == (1, 3, 384, 384) and x.dtype == np.float32
    assert x[0, 0, 0, 0] == pytest.approx((1 - 0.485) / 0.229, abs=1e-4)
    assert x[0, 2, 5, 5] == pytest.approx((1 - 0.406) / 0.225, abs=1e-4)
