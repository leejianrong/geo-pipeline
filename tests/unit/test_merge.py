import numpy as np

from geopipe.merge import merge_boxes, nms
from geopipe.stretch import apply_stretch, compute_stretch
from geopipe.tiling import has_data, windows


def box(x1, y1, x2, y2, score=0.9, cid=0):
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "score": score, "class_id": cid,
            "class_name": str(cid)}


def tile(x_off, boxes, w=640, h=640, y_off=0):
    return {"window": {"x_off": x_off, "y_off": y_off, "w": w, "h": h}, "boxes": boxes}


def test_nms_keeps_best_and_non_overlapping():
    xyxy = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=float)
    assert nms(xyxy, np.array([0.5, 0.9, 0.7]), 0.5) == [1, 2]


def test_duplicate_in_overlap_zone_is_merged():
    # Object at x 530..600 lies fully inside the 512..640 overlap of two tiles.
    per_tile = {0: tile(0, [box(530, 100, 600, 180)]), 1: tile(512, [box(18, 100, 88, 180)])}
    [b] = merge_boxes(per_tile, 1152, 640, overlap=0.2, iou=0.5)
    assert (b["x1"], b["x2"]) == (530, 600)


def test_object_on_a_seam_is_reported_once():
    # x 462..562 crosses the start of tile 1 (x=512): tile 1 only sees a clipped half.
    per_tile = {0: tile(0, [box(462, 100, 562, 180)]), 1: tile(512, [box(0, 100, 50, 180)])}
    [b] = merge_boxes(per_tile, 1152, 640, overlap=0.2, iou=0.5)
    assert (b["x1"], b["x2"]) == (462, 562)


def test_without_overlap_clipped_pieces_are_kept():
    per_tile = {0: tile(0, [box(600, 100, 640, 180)]), 1: tile(640, [box(0, 100, 60, 180)])}
    assert len(merge_boxes(per_tile, 1280, 640, overlap=0, iou=0.5)) == 2


def test_box_touching_the_raster_border_is_kept():
    per_tile = {0: tile(0, [box(0, 0, 50, 50)])}
    assert len(merge_boxes(per_tile, 640, 640, overlap=0.2, iou=0.5)) == 1


def test_different_classes_are_not_merged():
    per_tile = {0: tile(0, [box(10, 10, 50, 50, cid=1), box(10, 10, 50, 50, cid=2)])}
    assert len(merge_boxes(per_tile, 640, 640, overlap=0.2, iou=0.5)) == 2


def test_stretch_is_deterministic_and_maps_equal_values_equally():
    ov = np.stack([np.arange(10000, dtype=np.uint16).reshape(100, 100)] * 3)
    valid = np.ones((100, 100), bool)
    s = compute_stretch(ov, valid)
    assert s == compute_stretch(ov, valid)
    a = np.full((4, 4, 3), 5000, np.uint16)
    b = np.full((2, 9, 3), 5000, np.uint16)
    assert apply_stretch(a, s)[0, 0, 0] == apply_stretch(b, s)[0, 0, 0]
    assert apply_stretch(a, s).dtype == np.uint8


def test_stretch_ignores_invalid_pixels_and_never_divides_by_zero():
    ov = np.zeros((3, 10, 10), np.uint16)
    ov[:, :, 5:] = 100
    valid = np.zeros((10, 10), bool)
    valid[:, 5:] = True
    (lo, hi), *_ = compute_stretch(ov, valid)
    assert lo == 100 and hi > lo


def test_has_data_selects_windows_over_valid_area_only():
    valid = np.zeros((10, 20), bool)
    valid[:, :10] = True  # left half of a 2000 x 1000 raster
    wins = [w for w in windows(2000, 1000, 640, 0) if has_data(valid, w, 2000, 1000)]
    assert wins and all(w.x_off < 1000 for w in wins)
    assert len(wins) < len(windows(2000, 1000, 640, 0))
