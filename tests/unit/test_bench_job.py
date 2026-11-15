import numpy as np

from geopipe.bench import score_model
from geopipe.bench_job import OVERLAP, run_inprocess
from geopipe.messages import Box
from geopipe.tiling import windows

W, H = 2600, 700  # the right-hand tiles hold nothing
# objects wholly inside a tile, so no tile edge cuts them
GT = [[100, 100, 80, 60], [700, 200, 90, 90], [1000, 400, 100, 70], [300, 520, 60, 50]]
GT_COCO = {
    "images": [{"id": 1, "file_name": "a.png", "width": W, "height": H}],
    "annotations": [
        {"id": k, "image_id": 1, "category_id": 1, "bbox": b, "iscrowd": 0, "area": b[2] * b[3]}
        for k, b in enumerate(GT, start=1)
    ],
    "categories": [{"id": 1, "name": "thing"}],
}


def image() -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    for x, y, w, h in GT:
        img[y : y + h, x : x + w] = 255
    return img


class Oracle:
    """A perfect detector that knows the scene: given a tile it finds which window of the image
    it is and returns the ground-truth boxes that lie wholly inside it, in tile pixels."""

    def __init__(self, img):
        self.img, self.calls = img, 0

    def __call__(self, crop, conf):
        self.calls += 1
        for win in windows(W, H, 640, OVERLAP):
            if np.array_equal(
                self.img[win.y_off : win.y_off + win.h, win.x_off : win.x_off + win.w], crop
            ):
                return [
                    Box(
                        x1=x - win.x_off,
                        y1=y - win.y_off,
                        x2=x + w - win.x_off,
                        y2=y + h - win.y_off,
                        score=0.9,
                        class_id=0,
                        class_name="thing",
                    )
                    for x, y, w, h in GT
                    if win.x_off <= x
                    and x + w <= win.x_off + win.w
                    and win.y_off <= y
                    and y + h <= win.y_off + win.h
                ]
        raise AssertionError("a tile that is not a window of the image")


def test_tiles_are_cut_and_merged_so_a_perfect_detector_scores_perfectly():
    img = image()
    oracle = Oracle(img)
    runs = run_inprocess(oracle, [(1, img)], conf=0.05, iou=0.65)
    boxes = [f["properties"]["pixel_bbox"] for f in runs[1]["features"]]
    assert sorted(boxes) == sorted([[x, y, x + w, y + h] for x, y, w, h in GT])  # one each
    scored = score_model(GT_COCO, {"thing": 1}, runs, [1])
    assert (scored["tp"], scored["fp"], scored["fn"]) == (4, 0, 0)
    assert scored["precision"] == scored["recall"] == 1.0 and scored["map50"] == 1.0
    assert runs[1]["latency"] >= runs[1]["detect_seconds"] >= 0


def test_a_tile_of_nodata_is_not_sent_to_the_model():
    img = image()
    oracle = Oracle(img)
    run_inprocess(oracle, [(1, img)], conf=0.05, iou=0.65)
    total = len(windows(W, H, 640, OVERLAP))
    blank = sum(
        1
        for w in windows(W, H, 640, OVERLAP)
        if not img[w.y_off : w.y_off + w.h, w.x_off : w.x_off + w.w].any()
    )
    assert blank > 0 and oracle.calls == total - blank


def test_a_wrong_detector_scores_the_misses_and_false_alarms():
    img = image()

    def half_right(crop, conf):
        ys, xs = np.where(crop.max(axis=2) > 128)
        return (
            [Box(x1=5.0, y1=5.0, x2=20.0, y2=20.0, score=0.8, class_id=0, class_name="thing")]
            if xs.size
            else []
        )

    runs = run_inprocess(half_right, [(1, img)], conf=0.05, iou=0.65)
    scored = score_model(GT_COCO, {"thing": 1}, runs, [1])
    assert scored["tp"] == 0 and scored["fp"] > 0 and scored["fn"] == 4
