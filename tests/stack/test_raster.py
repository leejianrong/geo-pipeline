"""Slicer raster helpers against local files (needs GDAL, not the stack)."""

import sys
from pathlib import Path

import numpy as np
from conftest import build_tif
from osgeo import gdal

sys.path.insert(0, str(Path(__file__).parents[2] / "stages" / "slicer"))
from raster import open_raster, plan, read_tile  # noqa: E402

from geopipe.messages import Window  # noqa: E402
from geopipe.tiling import windows  # noqa: E402


def gradient_tif(path, bands, width=1500, height=1000):
    ds = gdal.GetDriverByName("GTiff").Create(path, width, height, bands, gdal.GDT_UInt16)
    ds.SetProjection('LOCAL_CS["x"]')
    ds.SetGeoTransform((0, 1, 0, 0, 0, -1))
    row = (np.arange(width) * 40).astype(np.uint16)  # 0..59960, value depends on x only
    for b in range(1, bands + 1):
        ds.GetRasterBand(b).WriteArray(np.tile(row, (height, 1)))
    ds = None


def test_uint16_tiles_use_one_stretch_so_equal_values_match_across_tiles(tmp_path):
    gradient_tif(str(tmp_path / "g.tif"), 3)
    r = open_raster(str(tmp_path / "g.tif"))
    a = read_tile(r, Window(x_off=0, y_off=0, w=640, h=640))
    b = read_tile(r, Window(x_off=500, y_off=0, w=640, h=640))
    assert a.dtype == np.uint8 and a.shape == (640, 640, 3)
    assert np.array_equal(a[0, 600], b[0, 100])  # both are global column 600
    assert a[0, 0, 0] < a[0, 639, 0]  # and the stretch is not flat


def test_single_band_raster_becomes_three_identical_channels(tmp_path):
    gradient_tif(str(tmp_path / "g1.tif"), 1)
    t = read_tile(open_raster(str(tmp_path / "g1.tif")), Window(x_off=0, y_off=0, w=64, h=64))
    assert t.shape == (64, 64, 3)
    assert np.array_equal(t[:, :, 0], t[:, :, 1]) and np.array_equal(t[:, :, 0], t[:, :, 2])


def test_plan_skips_windows_that_are_all_nodata(tmp_path):
    p = str(tmp_path / "n.tif")
    build_tif(p, 2000, 1280, [(0, 0, 1000, 1280)], value=10, nodata=0)  # right half is nodata
    r = open_raster(p)
    kept = plan(r, 640, 0)
    assert 0 < len(kept) < len(windows(2000, 1280, 640, 0))
    assert all(w.x_off < 1000 for w in kept)
