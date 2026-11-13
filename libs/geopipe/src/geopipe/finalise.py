"""Postprocess logic: collect tile detections and finalise a job, complete or timed out.

Imports osgeo, so only import it where GDAL exists (postprocess, tools).
"""

import json
import time

from osgeo import osr

from . import bus
from .geo import pixel_to_map
from .merge import merge_boxes
from .messages import DEADLINES, JOBS_DONE, Detection, Done

osr.UseExceptions()
LOCK_TTL = 300  # seconds; a crashed finaliser's lock expires and the message is retried


def _to_wgs84(wkt: str) -> osr.CoordinateTransformation:
    src, dst = osr.SpatialReference(), osr.SpatialReference()
    src.ImportFromWkt(wkt)
    dst.ImportFromEPSG(4326)
    for srs in (src, dst):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(src, dst)


def build_geojson(job_id: str, meta: dict, per_tile: dict[int, dict], missing: list[int]) -> dict:
    gt, to_ll = meta["geotransform"], _to_wgs84(meta["crs_wkt"])
    boxes = merge_boxes(per_tile, meta["width"], meta["height"], meta["overlap"], meta["iou"])
    features = []
    for b in boxes:
        corners = ((b["x1"], b["y1"]), (b["x2"], b["y1"]), (b["x2"], b["y2"]),
                   (b["x1"], b["y2"]), (b["x1"], b["y1"]))
        ring = [to_ll.TransformPoint(*pixel_to_map(gt, px, py))[:2] for px, py in corners]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]},
            "properties": {"class_name": b["class_name"], "class_id": b["class_id"],
                           "score": b["score"],
                           "pixel_bbox": [b["x1"], b["y1"], b["x2"], b["y2"]], "job_id": job_id}})
    return {"type": "FeatureCollection", "schema_version": 1, "job_id": job_id,
            "source_crs": meta["crs_wkt"], "model": meta["model"], "incomplete": bool(missing),
            "missing_tiles": missing, "features": features}


def finalise(r, s3, job_id: str) -> bool:
    """Write the job's GeoJSON and announce it with whatever detections have arrived.

    Marks the job `incomplete` (listing the missing tiles) if any tile is absent. Exclusive
    and idempotent: returns False if the job is already finished or another worker holds
    the finalise lock.
    """
    status_key = f"job:{job_id}:status"
    if r.hget(status_key, "state") in bus.TERMINAL:
        return False
    lock = f"job:{job_id}:finalise"
    if not r.set(lock, "1", nx=True, ex=LOCK_TTL):
        return False
    try:
        started = time.time()
        status = r.hgetall(status_key)
        tenant_id, total = status["tenant_id"], int(status["total"])
        bus.set_status(r, job_id, "finalising")
        meta = json.loads(r.get(f"job:{job_id}:meta"))
        dets_key = f"job:{job_id}:dets"
        per_tile = {int(i): json.loads(v) for i, v in r.hgetall(dets_key).items()}
        missing = [i for i in range(total) if i not in per_tile]
        geojson = build_geojson(job_id, meta, per_tile, missing)
        # Deterministic key: finalising twice overwrites the same object.
        out_key = f"tenants/{tenant_id}/results/{job_id}.geojson"
        s3.put_object(Bucket=meta["bucket"], Key=out_key, ContentType="application/geo+json",
                      Body=json.dumps(geojson).encode())
        for start in range(0, total, 1000):  # DeleteObjects accepts at most 1000 keys
            s3.delete_objects(Bucket="scratch", Delete={"Objects": [
                {"Key": f"tenants/{tenant_id}/{job_id}/{i}.png"}
                for i in range(start, min(start + 1000, total))]})
        r.delete(dets_key)
        uri = f"s3://{meta['bucket']}/{out_key}"
        state = "incomplete" if missing else "done"
        bus.set_status(
            r, job_id, state, output_uri=uri, finalise_seconds=round(time.time() - started, 3),
            boxes_raw=sum(len(t["boxes"]) for t in per_tile.values()),
            boxes_final=len(geojson["features"]), done_at=time.time())
        r.zrem(DEADLINES, job_id)
        bus.publish(r, JOBS_DONE, Done(tenant_id=tenant_id, job_id=job_id, status=state,
                                       output_uri=uri))
        return True
    except Exception:
        r.delete(lock)  # let the retry of this message take over
        raise


def handle_detection(r, s3, d: Detection) -> None:
    """Store one tile's boxes; finalise the job when every tile has reported."""
    if r.hget(f"job:{d.job_id}:status", "state") in bus.TERMINAL:
        return  # a late duplicate of a finished job
    dets_key = f"job:{d.job_id}:dets"
    # Keyed by tile index, so a redelivered tile overwrites itself instead of duplicating.
    r.hset(dets_key, d.idx, json.dumps({"window": d.window.model_dump(),
                                        "boxes": [b.model_dump() for b in d.boxes]}))
    r.expire(dets_key, bus.STATUS_TTL)
    bus.touch(r, d.job_id)
    if r.hlen(dets_key) >= d.total:
        finalise(r, s3, d.job_id)


def sweep_once(r, s3) -> None:
    """Finalise jobs that stopped making progress: partial results, or an error if the
    job never got as far as emitting tiles."""
    for job_id in r.zrangebyscore(DEADLINES, 0, time.time()):
        status = r.hgetall(f"job:{job_id}:status")
        if not status or status.get("state") in bus.TERMINAL:
            r.zrem(DEADLINES, job_id)
        elif not status.get("total"):
            bus.fail_job(r, status.get("tenant_id", "local"), job_id,
                         "timed out before slicing produced any tiles")
        else:
            finalise(r, s3, job_id)
