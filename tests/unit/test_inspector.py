from geopipe.inspector import build_inspect, inspect_key
from geopipe.merge import merge_traced


def box(x1, y1, x2, y2, score=0.9):
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "score": score, "class_id": 0,
            "class_name": "building"}


def test_inspect_lists_raw_boxes_per_tile_and_what_each_merged_box_absorbed():
    win = lambda x: {"x_off": x, "y_off": 0, "w": 640, "h": 640}  # noqa: E731
    per_tile = {0: {"window": win(0), "boxes": [box(530, 100, 600, 180, 0.9)]},
                1: {"window": win(512), "boxes": [box(18, 100, 88, 180, 0.7),
                                                box(0, 300, 50, 380)]}}
    meta = {"width": 1152, "height": 640, "overlap": 0.2, "iou": 0.5}
    merged, cut = merge_traced(per_tile, 1152, 640, 0.2, 0.5)
    doc = build_inspect("j1", meta, per_tile, merged, cut, [])
    assert doc["raster"] == [1152, 640] and doc["job_id"] == "j1"
    assert doc["tiles"][1]["raw"][0] == [18, 100, 88, 180, 0.7, 0]
    assert doc["tiles"][1]["cut"] == [1]
    assert doc["merged"] == [{"box": [530, 100, 600, 180], "score": 0.9, "class_id": 0,
                              "from": [[0, 0], [1, 0]]}]


def test_one_inspect_object_per_tenant():
    assert inspect_key("acme") == "tenants/acme/inspect/latest.json"
