"""Slicer: read a GeoTIFF window by window over /vsis3/, write PNG tiles, emit one message each."""

import io
import json
import logging
import resource
import socket
import time

from osgeo import gdal
from PIL import Image

from geopipe import bus, store
from geopipe.messages import JOBS_IN, TILES, Job, Tile
from geopipe.raster import open_raster, plan, read_tile

SCRATCH = "scratch"
META_TTL = 7 * 24 * 3600


def slice_job(r, s3, job: Job) -> None:
    started = time.time()
    bus.set_status(r, job.job_id, "slicing")
    # GDAL caches /vsis3/ listings and file state per process; a long-lived worker would
    # otherwise miss objects uploaded (or replaced) since its previous job.
    gdal.VSICurlClearCache()
    raster = open_raster(f"/vsis3/{job.bucket}/{job.key}")
    wins = plan(raster, job.options.tile_size, job.options.overlap)
    if not wins:
        raise ValueError("raster has no valid data")
    r.set(f"job:{job.job_id}:meta", json.dumps({
        "bucket": job.bucket, "key": job.key, "geotransform": raster.ds.GetGeoTransform(),
        "crs_wkt": raster.ds.GetProjection(), "model": job.options.model,
        "width": raster.width, "height": raster.height, "overlap": job.options.overlap,
        "iou": job.options.iou, "tile_size": job.options.tile_size,
        "stretch": raster.stretch}), ex=META_TTL)
    store.ensure_bucket(s3, SCRATCH)
    # Before the first tile is published, or it would reset the detector's counter.
    bus.set_status(r, job.job_id, "detecting", total=len(wins), tiles_detected=0)

    for idx, win in enumerate(wins):
        buf = io.BytesIO()
        Image.fromarray(read_tile(raster, win)).save(buf, "PNG")
        tile_key = f"tenants/{job.tenant_id}/{job.job_id}/{idx}.png"
        s3.put_object(Bucket=SCRATCH, Key=tile_key, Body=buf.getvalue())
        bus.publish(r, TILES, Tile(tenant_id=job.tenant_id, job_id=job.job_id, idx=idx,
                                   total=len(wins), window=win, tile_bucket=SCRATCH,
                                   tile_key=tile_key, options=job.options))
    # Plain hset, not set_status: the job may already be "done" by now.
    r.hset(f"job:{job.job_id}:status", mapping={
        "slice_seconds": round(time.time() - started, 2),
        "slicer_peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r, s3 = bus.connect(), store.client()
    bus.consume(r, JOBS_IN, "slicer", socket.gethostname(), Job, lambda j: slice_job(r, s3, j))
