"""Fixtures for tests that run inside the `tools` container against the compose stack."""

import pytest
from osgeo import gdal, osr

from geopipe import bus, store

gdal.UseExceptions()
BUCKET, KEY = "imagery", "synth.tif"
SIZE, GT, EPSG = 4096, (500000.0, 0.5, 0.0, 5000000.0, 0.0, -0.5), 32633
# One 100x80 bright rectangle inside the interior of five different 640 px grid cells,
# clear of every tile seam and of the shifted last row/column.
RECTS = [(cx * 640 + 200, cy * 640 + 200, 100, 80)
         for cx, cy in ((0, 0), (1, 2), (3, 1), (4, 4), (2, 3))]


def make_synth_tif(path: str) -> None:
    ds = gdal.GetDriverByName("GTiff").Create(path, SIZE, SIZE, 3, gdal.GDT_Byte,
                                              ["TILED=YES", "COMPRESS=DEFLATE"])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform(GT)
    for b in (1, 2, 3):
        band = ds.GetRasterBand(b)
        for x, y, w, h in RECTS:
            band.WriteRaster(x, y, w, h, b"\xff" * (w * h))
    ds.FlushCache()


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    r, s3 = bus.connect(), store.client()
    store.ensure_bucket(s3, BUCKET)
    path = str(tmp_path_factory.mktemp("fx") / KEY)
    make_synth_tif(path)
    s3.upload_file(path, BUCKET, KEY)
    return r, s3, path
