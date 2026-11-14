import pytest

from geopipe.spacenet import MIN_SIDE, polygon_bbox, split

GT = (32.0, 0.0000027, 0.0, 15.0, 0.0, -0.0000027)  # origin lon/lat, 0.3 m pixels, north-up


def ring(*pixels):
    """Polygon ring given in pixel coordinates, as lon/lat vertices with a dummy z."""
    return [[[GT[0] + x * GT[1], GT[3] + y * GT[5], 0.0] for x, y in pixels]]


def test_box_encloses_the_polygon_and_round_trips_to_pixels():
    x, y, w, h = polygon_bbox(ring((10, 20), (50.5, 22), (48, 60.25), (12, 58)), GT, 650, 650)
    assert (x, y) == pytest.approx((10, 20), abs=1e-6)
    assert (x + w, y + h) == pytest.approx((50.5, 60.25), abs=1e-6)


def test_box_is_clipped_to_the_tile_and_slivers_are_dropped():
    x, y, w, h = polygon_bbox(ring((-5, 600), (30, 600), (30, 700), (-5, 700)), GT, 650, 650)
    assert (x, y, x + w, y + h) == pytest.approx((0, 600, 30, 650))
    assert polygon_bbox(ring((100, 100), (100 + MIN_SIDE / 2, 100),
                             (100 + MIN_SIDE / 2, 140), (100, 140)), GT, 650, 650) is None
    assert polygon_bbox([], GT, 650, 650) is None


def test_split_is_contiguous_and_disjoint():
    parts = split(list(range(100)), valid=10, test=20)
    assert parts["train"] == list(range(70))
    assert parts["valid"] == list(range(70, 80))
    assert parts["test"] == list(range(80, 100))
