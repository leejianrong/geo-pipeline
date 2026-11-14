import pytest

from geopipe.viewer import filter_features, overlay_boxes


def feat(name, score, box=(0, 0, 10, 10)):
    return {"properties": {"class_name": name, "score": score, "pixel_bbox": list(box)}}


FEATS = [feat("cat", 0.9), feat("cat", 0.4), feat("couch", 0.7), feat("building", 0.2)]


def test_score_filter_is_inclusive_and_shrinks_the_set():
    assert len(filter_features(FEATS, 0.0)) == 4
    assert [f["properties"]["score"] for f in filter_features(FEATS, 0.4)] == [0.9, 0.4, 0.7]
    assert filter_features(FEATS, 0.95) == []


def test_class_filter_matches_substrings_in_any_case():
    assert len(filter_features(FEATS, 0.0, "CAT")) == 2
    assert len(filter_features(FEATS, 0.5, "c")) == 2  # cat 0.9 and couch 0.7
    assert len(filter_features(FEATS, 0.0, " ")) == 4


def test_boxes_are_fractions_of_the_image_with_a_label():
    [b] = overlay_boxes([feat("cat", 0.912, (100, 50, 300, 250))], width=1000, height=500)
    assert (b["x"], b["y"], b["w"], b["h"]) == pytest.approx((0.1, 0.1, 0.2, 0.4))
    assert b["label"] == "cat 0.91" and b["score"] == 0.912
