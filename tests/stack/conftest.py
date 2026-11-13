"""Fixtures for tests that run inside the `tools` container against the compose stack."""

import os
import urllib.request

import numpy as np
import pytest
from osgeo import gdal, osr
from PIL import Image

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


# COCO val2017 image 39769 (two cats on a couch), CC BY 2.0 via Flickr; fetched, never committed.
PHOTO_URL = "http://images.cocodataset.org/val2017/000000039769.jpg"


def upload_photo_tif(s3, key: str = "cats.tif") -> str:
    """Wrap the photo in a georeferenced GeoTIFF (arbitrary CRS, 1 m pixels) and upload it."""
    cache = "/data/cache" if os.path.isdir("/data") else "/tmp"
    os.makedirs(cache, exist_ok=True)
    jpg = f"{cache}/cats.jpg"
    if not os.path.exists(jpg):
        urllib.request.urlretrieve(PHOTO_URL, jpg)
    arr = np.array(Image.open(jpg).convert("RGB"))
    tif = f"{cache}/cats.tif"
    h, w, _ = arr.shape
    ds = gdal.GetDriverByName("GTiff").Create(tif, w, h, 3, gdal.GDT_Byte)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform((500000.0, 1.0, 0.0, 5000000.0, 0.0, -1.0))
    for b in range(3):
        ds.GetRasterBand(b + 1).WriteArray(arr[:, :, b])
    ds = None  # close: the GeoTIFF is incomplete on disk until the dataset is released
    s3.upload_file(tif, BUCKET, key)
    return key
