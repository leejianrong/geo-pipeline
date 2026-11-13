from geopipe.tiling import windows


def test_covers_raster_exactly_with_full_size_tiles():
    wins = windows(4096, 3000, 640, 0.2)
    assert {(w.w, w.h) for w in wins} == {(640, 640)}
    for x in range(0, 4096, 37):
        for y in range(0, 3000, 37):
            assert any(w.x_off <= x < w.x_off + w.w and w.y_off <= y < w.y_off + w.h
                       for w in wins), (x, y)
    assert max(w.x_off + w.w for w in wins) == 4096
    assert max(w.y_off + w.h for w in wins) == 3000


def test_last_tile_shifts_inward_on_odd_sizes():
    assert [w.x_off for w in windows(1000, 640, 640, 0)] == [0, 360]


def test_raster_smaller_than_tile_gets_one_window():
    [w] = windows(300, 200, 640, 0.2)
    assert (w.x_off, w.y_off, w.w, w.h) == (0, 0, 300, 200)


def test_zero_overlap_grid_is_row_major():
    wins = windows(1280, 1280, 640, 0)
    assert [(w.x_off, w.y_off) for w in wins] == [(0, 0), (640, 0), (0, 640), (640, 640)]
