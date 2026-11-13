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


def build_tif(path: str, width: int, height: int, rects, bands: int = 3,
              dtype=gdal.GDT_Byte, value: int = 255, nodata: int | None = None,
              options=("TILED=YES", "COMPRESS=DEFLATE", "SPARSE_OK=TRUE")) -> None:
    """Georeferenced GeoTIFF, zero everywhere except bright rectangles."""
    ds = gdal.GetDriverByName("GTiff").Create(path, width, height, bands, dtype, list(options))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform(GT)
    itemsize = {gdal.GDT_Byte: 1, gdal.GDT_UInt16: 2}[dtype]
    pixel = int(value).to_bytes(itemsize, "little")
    for b in range(1, bands + 1):
        band = ds.GetRasterBand(b)
        if nodata is not None:
            band.SetNoDataValue(nodata)
        for x, y, w, h in rects:
            band.WriteRaster(x, y, w, h, pixel * (w * h))
    ds = None  # close: the GeoTIFF is incomplete on disk until the dataset is released


def make_synth_tif(path: str) -> None:
    build_tif(path, SIZE, SIZE, RECTS)


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


def run_job(r, s3, key: str, timeout: int = 120, **options):
    """Submit a job, wait for done, return (status, geojson)."""
    import json
    import time

    from geopipe.jobs import submit_job
    from geopipe.messages import Options

    job_id = submit_job(r, s3, BUCKET, key, options=Options(**options))
    deadline = time.time() + timeout
    while r.hget(f"job:{job_id}:status", "state") != "done":
        assert time.time() < deadline, r.hgetall(f"job:{job_id}:status")
        time.sleep(1)
    status = r.hgetall(f"job:{job_id}:status")
    bucket, out_key = status["output_uri"].removeprefix("s3://").split("/", 1)
    return status, json.loads(s3.get_object(Bucket=bucket, Key=out_key)["Body"].read())
