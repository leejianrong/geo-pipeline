import json
from pathlib import Path

import pytest

from geopipe.bench import evaluate, match_at_iou, pick_images, to_coco_detections

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "coco_mini.json").read_text())


def gt_for(*boxes, crowd=()):
    anns = [{"id": i + 1, "image_id": 1, "category_id": 1, "bbox": list(b), "area": b[2] * b[3],
             "iscrowd": 0} for i, b in enumerate(boxes)]
    anns += [{"id": 100 + i, "image_id": 1, "category_id": 1, "bbox": list(b),
              "area": b[2] * b[3], "iscrowd": 1} for i, b in enumerate(crowd)]
    return {"images": [{"id": 1, "width": 100, "height": 100}], "annotations": anns,
            "categories": [{"id": 1, "name": "thing"}]}


def det(box, score):
    return {"image_id": 1, "category_id": 1, "bbox": list(box), "score": score}


def test_precision_recall_f1_on_a_hand_computed_case():
    gt = gt_for((0, 0, 10, 10), (20, 20, 10, 10))
    dets = [det((0, 0, 10, 10), 0.9),    # true positive, IoU 1.0
            det((50, 50, 10, 10), 0.8),  # false positive
            det((20, 20, 10, 10), 0.3)]  # below min_score: ignored (so the second box is missed)
    m = match_at_iou(gt, dets, [1])
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)
    assert (m["precision"], m["recall"], m["f1"]) == (0.5, 0.5, 0.5)
    assert m["ious"] == [1.0]


def test_a_duplicate_detection_of_one_object_is_a_false_positive():
    dets = [det((0, 0, 10, 10), 0.9), det((0, 0, 10, 10), 0.8)]
    m = match_at_iou(gt_for((0, 0, 10, 10)), dets, [1])
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 0)


def test_detections_inside_a_crowd_region_are_ignored():
    gt = gt_for((0, 0, 10, 10), crowd=[(40, 40, 50, 50)])
    m = match_at_iou(gt, [det((0, 0, 10, 10), 0.9), det((50, 50, 10, 10), 0.8)], [1])
    assert (m["tp"], m["fp"], m["fn"]) == (1, 0, 0)


def test_perfect_detections_score_map_one():
    gt = gt_for((0, 0, 10, 10), (20, 20, 30, 30))
    result = evaluate(gt, [det((0, 0, 10, 10), 0.9), det((20, 20, 30, 30), 0.8)], [1])
    assert result["map"] == pytest.approx(1.0) and result["map50"] == pytest.approx(1.0)


def test_no_detections_score_zero_without_crashing():
    assert evaluate(gt_for((0, 0, 10, 10)), [], [1])["map"] == 0.0


def test_geojson_features_become_coco_detections_and_unknown_classes_are_dropped():
    feats = [{"properties": {"pixel_bbox": [10, 20, 40, 60], "score": 0.7, "class_name": "cat"}},
             {"properties": {"pixel_bbox": [0, 0, 5, 5], "score": 0.9, "class_name": "unicorn"}}]
    assert to_coco_detections(7, feats, {"cat": 17}) == [
        {"image_id": 7, "category_id": 17, "bbox": [10, 20, 30, 40], "score": 0.7}]


def test_pick_images_is_deterministic_and_skips_unlabelled_images():
    coco = {"images": [{"id": 3}, {"id": 1}, {"id": 2}],
            "annotations": [{"image_id": 1, "iscrowd": 0}, {"image_id": 3, "iscrowd": 0},
                            {"image_id": 2, "iscrowd": 1}]}
    assert [i["id"] for i in pick_images(coco, 10)] == [1, 3]
    assert [i["id"] for i in pick_images(coco, 1)] == [1]
    assert len(pick_images(FIXTURE, 10)) == 5
