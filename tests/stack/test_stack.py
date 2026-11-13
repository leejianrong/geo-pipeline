import json
import os
import shutil
import time
import urllib.request

from conftest import BUCKET, EPSG, GT, KEY, RECTS, SIZE
from osgeo import gdal, osr

from geopipe import store
from geopipe.jobs import submit_job
from geopipe.messages import Options


def test_gdal_range_reads_and_presigned_get_work_against_the_store(stack):
    _, s3, _ = stack
    ds = gdal.Open(f"/vsis3/{BUCKET}/{KEY}")
    assert (ds.RasterXSize, ds.RasterYSize) == (SIZE, SIZE)
    x, y, w, h = RECTS[0]
    assert ds.GetRasterBand(1).ReadAsArray(x - 10, y - 10, w + 20, h + 20).max() == 255
    with urllib.request.urlopen(store.presign_get(s3, BUCKET, KEY)) as resp:
        assert resp.read(4) in (b"II*\x00", b"MM\x00*")


def test_round_trip_boxes_land_on_the_drawn_rectangles(stack):
    r, s3, local_tif = stack
    job_id = submit_job(r, s3, BUCKET, KEY, options=Options(overlap=0))
    deadline = time.time() + 90
    while r.hget(f"job:{job_id}:status", "state") != "done":
        assert time.time() < deadline, r.hgetall(f"job:{job_id}:status")
        time.sleep(1)

    status = r.hgetall(f"job:{job_id}:status")
    bucket, key = status["output_uri"].removeprefix("s3://").split("/", 1)
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    fc = json.loads(body)
    assert fc["incomplete"] is False and len(fc["features"]) == len(RECTS)
    assert status["tiles_detected"] == status["total"]

    src, ll = osr.SpatialReference(), osr.SpatialReference()
    src.ImportFromEPSG(EPSG)
    ll.ImportFromEPSG(4326)
    for s in (src, ll):
        s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    back = osr.CoordinateTransformation(ll, src)

    for x, y, w, h in RECTS:
        want_centre = (GT[0] + (x + w / 2) * GT[1], GT[3] + (y + h / 2) * GT[5])
        [feat] = [f for f in fc["features"]
                  if all(abs(a - b) <= 1 for a, b in zip(f["properties"]["pixel_bbox"],
                                                         (x, y, x + w, y + h), strict=True))]
        ring = feat["geometry"]["coordinates"][0][:4]
        cx, cy, _ = back.TransformPoint(sum(p[0] for p in ring) / 4, sum(p[1] for p in ring) / 4)
        assert abs(cx - want_centre[0]) <= abs(GT[1]) and abs(cy - want_centre[1]) <= abs(GT[1])

    if os.path.isdir("/data"):  # leave artefacts for inspection in QGIS (`make e2e`)
        os.makedirs("/data/out", exist_ok=True)
        shutil.copy(local_tif, "/data/out/synth.tif")
        with open("/data/out/result.geojson", "wb") as f:
            f.write(body)
