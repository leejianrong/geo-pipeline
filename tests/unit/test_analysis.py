import math

from geopipe.analysis import distributions, tile_counts, timings
from geopipe.messages import Window


def feat(x1, y1, x2, y2, score=0.9, name="car"):
    return {"properties": {"pixel_bbox": [x1, y1, x2, y2], "score": score, "class_name": name}}


def test_distributions_from_features():
    d = distributions([feat(0, 0, 30, 40, 0.8), feat(0, 0, 10, 10, 0.6, "boat"), feat(1, 1, 2, 2)])
    assert d["scores"] == [0.8, 0.6, 0.9]
    assert d["sizes"][:2] == [math.sqrt(1200), 10.0]
    assert d["classes"] == {"car": 2, "boat": 1}


def test_distributions_without_features():
    assert distributions([]) == {"scores": [], "sizes": [], "classes": {}}


def test_tile_counts_assign_boxes_by_centre_and_share_overlap():
    wins = [Window(x_off=0, y_off=0, w=100, h=100), Window(x_off=80, y_off=0, w=100, h=100)]
    feats = [feat(10, 10, 20, 20), feat(85, 10, 95, 20), feat(150, 10, 160, 20)]
    # the middle box sits in the overlap, so both windows count it
    assert tile_counts(wins, feats) == [2, 2]
    assert tile_counts(wins, []) == [0, 0]


def test_timings_and_throughput():
    status = {"total": "10", "slice_seconds": "2", "detect_seconds": "5.5",
              "finalise_seconds": "0.5", "submitted_at": "100", "done_at": "110",
              "boxes_raw": "7", "boxes_final": "5"}
    t = timings(status, {"width": 2000, "height": 1000})
    assert t["wall"] == 10 and t["tiles_per_s"] == 1.0 and t["mpix_per_s"] == 0.2
    assert (t["slice"], t["detect"], t["finalise"], t["boxes_raw"], t["boxes_final"]) == (
        2, 5.5, 0.5, 7, 5)


def test_timings_without_submit_time_has_no_wall_or_throughput():
    t = timings({"total": "4"}, {"width": 10, "height": 10})
    assert t["wall"] is None and t["tiles_per_s"] is None
