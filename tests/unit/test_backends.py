import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[2] / "stages" / "detector"))
from backends import stub_detect, to_boxes  # noqa: E402


def test_to_boxes_normalises_rfdetr_detections():
    det = SimpleNamespace(
        xyxy=np.array([[1.0, 2.0, 30.0, 40.0], [5.0, 6.0, 7.0, 8.0]], dtype=np.float32),
        confidence=np.array([0.9, 0.4], dtype=np.float32),
        class_id=np.array([17, 62]),
        data={"class_name": np.array(["cat", "tv"])})
    boxes = to_boxes(det)
    assert [(b.class_id, b.class_name) for b in boxes] == [(17, "cat"), (62, "tv")]
    assert boxes[0].x2 == 30.0 and abs(boxes[1].score - 0.4) < 1e-6


def test_to_boxes_handles_no_detections():
    # supervision's empty Detections has no class_name entry in .data
    det = SimpleNamespace(xyxy=np.empty((0, 4)), confidence=np.empty(0),
                          class_id=np.empty(0), data={})
    assert to_boxes(det) == []


def test_stub_boxes_bright_pixels_only():
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[10:30, 20:60] = 255
    [b] = stub_detect(img, 0.25)
    assert (b.x1, b.y1, b.x2, b.y2) == (20, 10, 60, 30)
    assert stub_detect(np.zeros((8, 8, 3), dtype=np.uint8), 0.25) == []
