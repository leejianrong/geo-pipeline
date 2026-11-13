"""Slicer: read a GeoTIFF window by window over /vsis3/, write PNG tiles, emit one message each."""

import io
import json
import logging
import socket

import numpy as np
from osgeo import gdal
from PIL import Image

from geopipe import bus, store
from geopipe.messages import JOBS_IN, TILES, Job, Tile
from geopipe.tiling import windows

SCRATCH = "scratch"
META_TTL = 7 * 24 * 3600
gdal.UseExceptions()


def slice_job(r, s3, job: Job) -> None:
    bus.set_status(r, job.job_id, "slicing")
    # GDAL caches /vsis3/ listings and file state per process; a long-lived worker would
    # otherwise miss objects uploaded (or replaced) since its previous job.
    gdal.VSICurlClearCache()
    ds = gdal.Open(f"/vsis3/{job.bucket}/{job.key}")
    wkt = ds.GetProjection()
    if not wkt:
        raise ValueError("raster has no CRS")
    bands = [ds.GetRasterBand(b) for b in ((1, 2, 3) if ds.RasterCount >= 3 else (1, 1, 1))]
    if bands[0].DataType != gdal.GDT_Byte:
        raise ValueError("only uint8 rasters are supported until V3")

    wins = windows(ds.RasterXSize, ds.RasterYSize, job.options.tile_size, job.options.overlap)
    r.set(f"job:{job.job_id}:meta", json.dumps({
        "bucket": job.bucket, "key": job.key, "geotransform": ds.GetGeoTransform(),
        "crs_wkt": wkt, "model": job.options.model}), ex=META_TTL)
    store.ensure_bucket(s3, SCRATCH)
    # Before the first tile is published, or it would reset the detector's counter.
    bus.set_status(r, job.job_id, "detecting", total=len(wins), tiles_detected=0)

    for idx, win in enumerate(wins):
        arr = np.dstack([b.ReadAsArray(win.x_off, win.y_off, win.w, win.h) for b in bands])
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, "PNG")
        tile_key = f"tenants/{job.tenant_id}/{job.job_id}/{idx}.png"
        s3.put_object(Bucket=SCRATCH, Key=tile_key, Body=buf.getvalue())
        bus.publish(r, TILES, Tile(tenant_id=job.tenant_id, job_id=job.job_id, idx=idx,
                                   total=len(wins), window=win, tile_bucket=SCRATCH,
                                   tile_key=tile_key, options=job.options))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r, s3 = bus.connect(), store.client()
    bus.consume(r, JOBS_IN, "slicer", socket.gethostname(), Job, lambda j: slice_job(r, s3, j))
