"""Postprocess: collect per-tile boxes, georeference them, write GeoJSON, announce completion."""

import json
import logging
import socket

from osgeo import osr

from geopipe import bus, store
from geopipe.geo import pixel_to_map
from geopipe.merge import merge_boxes
from geopipe.messages import DETECTIONS, JOBS_DONE, Detection, Done

osr.UseExceptions()


def _to_wgs84(wkt: str) -> osr.CoordinateTransformation:
    src, dst = osr.SpatialReference(), osr.SpatialReference()
    src.ImportFromWkt(wkt)
    dst.ImportFromEPSG(4326)
    for srs in (src, dst):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(src, dst)


def build_geojson(job_id: str, meta: dict, per_tile: dict[int, dict]) -> dict:
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
            "source_crs": meta["crs_wkt"], "model": meta["model"], "incomplete": False,
            "missing_tiles": [], "features": features}


def handle(r, s3, d: Detection) -> None:
    dets_key = f"job:{d.job_id}:dets"
    r.hset(dets_key, d.idx, json.dumps({"window": d.window.model_dump(),
                                        "boxes": [b.model_dump() for b in d.boxes]}))
    if r.hlen(dets_key) < d.total:
        return
    bus.set_status(r, d.job_id, "finalising")
    meta = json.loads(r.get(f"job:{d.job_id}:meta"))
    per_tile = {int(i): json.loads(v) for i, v in r.hgetall(dets_key).items()}
    out_key = f"tenants/{d.tenant_id}/results/{d.job_id}.geojson"
    s3.put_object(Bucket=meta["bucket"], Key=out_key, ContentType="application/geo+json",
                  Body=json.dumps(build_geojson(d.job_id, meta, per_tile)).encode())
    for start in range(0, d.total, 1000):  # DeleteObjects accepts at most 1000 keys
        s3.delete_objects(Bucket="scratch", Delete={"Objects": [
            {"Key": f"tenants/{d.tenant_id}/{d.job_id}/{i}.png"}
            for i in range(start, min(start + 1000, d.total))]})
    r.delete(dets_key)
    uri = f"s3://{meta['bucket']}/{out_key}"
    bus.set_status(r, d.job_id, "done", output_uri=uri)
    bus.publish(r, JOBS_DONE, Done(tenant_id=d.tenant_id, job_id=d.job_id, status="done",
                                   output_uri=uri))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r, s3 = bus.connect(), store.client()
    bus.consume(r, DETECTIONS, "postprocess", socket.gethostname(), Detection,
                lambda d: handle(r, s3, d))
