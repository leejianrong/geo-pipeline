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


def test_dataset_stats_count_empty_images_and_bin_boxes_by_count_and_size():
    from geopipe.viewer import BOX_COUNT_EDGES, BOX_SIZE_EDGES, dataset_stats, edge_labels

    coco = {"images": [{"id": 1}, {"id": 2}, {"id": 3}],
            "annotations": [{"image_id": 1, "bbox": [0, 0, 4, 4]},       # size 4
                            {"image_id": 1, "bbox": [0, 0, 20, 20]},     # size 20
                            {"image_id": 2, "bbox": [0, 0, 500, 500]}]}  # size 500, one huge box
    s = dataset_stats(coco)
    assert (s["images"], s["boxes"], s["empty_images"]) == (3, 3, 1)
    assert s["boxes_per_image"] == [1, 2, 0, 0, 0, 0]  # image 3 has 0, images 2 and 1 have 1 and 2
    assert s["box_sizes"] == [1, 0, 1, 0, 0, 1]
    assert len(edge_labels(BOX_COUNT_EDGES)) == len(s["boxes_per_image"]) == len(BOX_COUNT_EDGES)
    assert len(edge_labels(BOX_SIZE_EDGES)) == len(s["box_sizes"])
    assert dataset_stats({"images": [], "annotations": []})["boxes_per_image"] == [0] * 6


def test_image_matching_agrees_with_the_benchmark_scorer():
    import random

    from geopipe import bench
    from geopipe.viewer import image_outcomes

    rnd = random.Random(7)

    def box():
        return [rnd.uniform(0, 80), rnd.uniform(0, 80), rnd.uniform(5, 30), rnd.uniform(5, 30)]

    images = [{"id": i, "file_name": f"{i}.png", "width": 120, "height": 120} for i in range(1, 9)]
    anns = [{"id": 100 * i + k, "image_id": i, "category_id": 1, "bbox": box(), "iscrowd": 0}
            for i in range(1, 9) for k in range(rnd.randint(0, 6))]
    dets = [{"image_id": a["image_id"], "category_id": 1, "score": rnd.random(),
             "bbox": [a["bbox"][0] + rnd.uniform(-4, 4), a["bbox"][1] + rnd.uniform(-4, 4),
                      a["bbox"][2], a["bbox"][3]]}
            for a in anns if rnd.random() < 0.8]
    dets += [{"image_id": rnd.randint(1, 8), "category_id": 1, "score": rnd.random(),
              "bbox": box()} for _ in range(15)]
    gt = {"images": images, "annotations": anns}
    preds: dict[str, list] = {}
    for d in dets:
        preds.setdefault(f"{d['image_id']}.png", []).append(
            {"bbox": d["bbox"], "score": d["score"]})
    for thr in (0.0, 0.5, 0.8):
        want = bench.match_at_iou(gt, dets, [i["id"] for i in images], 0.5, thr)
        rows = image_outcomes(gt, preds, thr)
        assert [sum(r[k] for r in rows) for k in ("tp", "fp", "fn")] == [
            want["tp"], want["fp"], want["fn"]]


def test_matching_marks_hits_false_alarms_and_misses_and_sorts_the_worst_first():
    from geopipe.viewer import image_outcomes, match_image, match_overlay, prediction_features

    gt = [[0, 0, 10, 10], [50, 50, 10, 10]]
    feats = prediction_features([{"bbox": [1, 1, 10, 10], "score": 0.9},
                                 {"bbox": [20, 20, 5, 5], "score": 0.6}])
    m = match_image(gt, feats)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)
    labels = [b["label"] for b in match_overlay(m, 100, 100)]
    assert labels == ["TP 0.90", "FP 0.60", "missed"]
    coco = {"images": [{"id": 1, "file_name": "ok.png"}, {"id": 2, "file_name": "bad.png"}],
            "annotations": [{"image_id": 1, "bbox": gt[0]}, {"image_id": 2, "bbox": gt[1]}]}
    preds = {"ok.png": [{"bbox": gt[0], "score": 0.9}]}
    rows = image_outcomes(coco, preds, 0.5)
    assert [r["file"] for r in rows] == ["bad.png", "ok.png"]
    assert image_outcomes(coco, preds, 0.95)[0]["fn"] == 1
