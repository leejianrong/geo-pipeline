import json
import os
import shutil
import time
import urllib.request

import pytest
from conftest import BUCKET, EPSG, GT, KEY, RECTS, SIZE, build_tif, run_job, upload_photo_tif
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


@pytest.mark.parametrize("model", ["rfdetr", "yolox", "yolox-tiny", "rfdetr-nano"])
def test_each_model_finds_both_cats_in_a_real_photo(stack, model):
    r, s3, _ = stack
    key = upload_photo_tif(s3)
    job_id = submit_job(r, s3, BUCKET, key, options=Options(model=model, overlap=0, conf=0.5))
    deadline = time.time() + 180
    while r.hget(f"job:{job_id}:status", "state") != "done":
        assert time.time() < deadline, r.hgetall(f"job:{job_id}:status")
        time.sleep(1)

    uri = r.hget(f"job:{job_id}:status", "output_uri")
    bucket, out_key = uri.removeprefix("s3://").split("/", 1)
    fc = json.loads(s3.get_object(Bucket=bucket, Key=out_key)["Body"].read())
    assert fc["model"] == model
    cats = [f for f in fc["features"] if f["properties"]["class_name"] == "cat"]
    assert len(cats) >= 2 and all(f["properties"]["score"] >= 0.5 for f in cats)
    assert all(0 <= v <= 640 for f in fc["features"] for v in f["properties"]["pixel_bbox"])


def test_object_on_a_tile_seam_is_reported_once_with_default_overlap(stack, tmp_path):
    r, s3, _ = stack
    path = str(tmp_path / "seam.tif")
    # Default tiles are 640 px with 512 px stride; x 462..562 straddles the second tile's edge.
    build_tif(path, 1280, 1280, [(462, 300, 100, 80)])
    s3.upload_file(path, BUCKET, "seam.tif")
    _, fc = run_job(r, s3, "seam.tif")
    [feat] = fc["features"]
    assert feat["properties"]["pixel_bbox"] == [462, 300, 562, 380]


def test_job_total_counts_only_tiles_that_contain_data(stack, tmp_path):
    r, s3, _ = stack
    path = str(tmp_path / "half.tif")
    build_tif(path, 2000, 1280, [(100, 100, 100, 80)], value=255, nodata=0)  # all else nodata
    s3.upload_file(path, BUCKET, "half.tif")
    status, fc = run_job(r, s3, "half.tif", overlap=0)
    assert int(status["total"]) == 1 and len(fc["features"]) == 1


def test_job_with_more_than_1000_tiles_completes(stack, tmp_path):
    # S3 DeleteObjects takes at most 1000 keys; cleanup of a bigger job must be chunked.
    r, s3, _ = stack
    path = str(tmp_path / "many.tif")
    build_tif(path, 2112, 2112, [(70, 70, 20, 20)])
    s3.upload_file(path, BUCKET, "many.tif")
    status, fc = run_job(r, s3, "many.tif", tile_size=64, overlap=0)
    assert int(status["total"]) == 33 * 33 and len(fc["features"]) == 1
    leftover = s3.list_objects_v2(Bucket="scratch", Prefix=f"tenants/local/{fc['job_id']}/")
    assert leftover["KeyCount"] == 0


def test_cli_upload_and_download_round_trip(stack, tmp_path):
    import subprocess
    src = tmp_path / "hello.bin"
    src.write_bytes(b"geo-pipeline")
    subprocess.run(["geopipe", "upload", str(src), f"s3://{BUCKET}/cli/hello.bin"], check=True)
    out = tmp_path / "back.bin"
    subprocess.run(["geopipe", "download", f"s3://{BUCKET}/cli/hello.bin", str(out)], check=True)
    assert out.read_bytes() == b"geo-pipeline"
