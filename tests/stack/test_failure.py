"""Failure behaviour (V4) against the live stack: retries, dead letters, errors, timeouts."""

import json
import threading
import time
import uuid

import pytest
from botocore.exceptions import ClientError
from conftest import BUCKET, EPSG, GT, run_job
from osgeo import osr

from geopipe import bus
from geopipe.finalise import finalise
from geopipe.jobs import submit_job
from geopipe.messages import (
    DETECTIONS,
    JOBS_DEAD,
    JOBS_DONE,
    TILES,
    Box,
    Detection,
    Job,
    Options,
    Tile,
    Window,
)


def wait_for(fn, timeout=60, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if value := fn():
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {what}")


def state(r, job_id):
    return r.hget(f"job:{job_id}:status", "state")


def entries(r, stream, needle):
    """Entries of a stream whose raw data mentions `needle`."""
    return [(i, f) for i, f in r.xrange(stream) if needle in f.get("data", "")]


def fabricate_job(r, total, timeout_seconds=300, tenant="local"):
    """State of a job whose tiles were sliced elsewhere, so tests control what arrives."""
    job_id = f"test-{uuid.uuid4().hex[:10]}"
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    r.set(f"job:{job_id}:meta", json.dumps({
        "bucket": BUCKET, "key": "fabricated.tif", "geotransform": GT,
        "crs_wkt": srs.ExportToWkt(), "model": "stub", "width": 1280, "height": 640,
        "overlap": 0.0, "iou": 0.5, "tile_size": 640, "stretch": None}))
    bus.set_status(r, job_id, "detecting", total=total, tiles_detected=0, tenant_id=tenant,
                   timeout_seconds=timeout_seconds)
    return job_id


def detection(job_id, idx, total):
    box = Box(x1=10, y1=10, x2=50, y2=50, score=0.9, class_id=0, class_name="thing")
    return Detection(tenant_id="local", job_id=job_id, idx=idx, total=total,
                     window=Window(x_off=idx * 640, y_off=0, w=640, h=640), boxes=[box])


def test_corrupt_raster_reports_an_error_without_output(stack):
    r, s3, _ = stack
    s3.put_object(Bucket=BUCKET, Key="corrupt.tif", Body=b"this is not a tiff " * 50)
    job_id = submit_job(r, s3, BUCKET, "corrupt.tif")
    wait_for(lambda: state(r, job_id) == "error", 30, "error state")
    assert "cannot read raster" in r.hget(f"job:{job_id}:status", "error")
    assert entries(r, JOBS_DONE, job_id), "no error event on jobs:done"
    with pytest.raises(ClientError):
        s3.head_object(Bucket=BUCKET, Key=f"tenants/local/results/{job_id}.geojson")


def test_raster_without_a_crs_reports_an_error(stack, tmp_path):
    r, s3, _ = stack
    from osgeo import gdal
    path = str(tmp_path / "nocrs.tif")
    ds = gdal.GetDriverByName("GTiff").Create(path, 64, 64, 3, gdal.GDT_Byte)
    ds = None  # noqa: F841 - closing writes the file
    s3.upload_file(path, BUCKET, "nocrs.tif")
    job_id = submit_job(r, s3, BUCKET, "nocrs.tif")
    wait_for(lambda: state(r, job_id) == "error", 30, "error state")
    assert "no CRS" in r.hget(f"job:{job_id}:status", "error")


def test_unknown_model_fails_the_job_without_retries(stack):
    r, s3, _ = stack
    job_id = submit_job(r, s3, BUCKET, "synth.tif", options=Options(model="nope", overlap=0))
    wait_for(lambda: state(r, job_id) == "error", 30, "error state")
    assert "not enabled" in r.hget(f"job:{job_id}:status", "error")


def test_submitting_the_same_job_twice_gives_one_job_and_one_output(stack):
    r, s3, _ = stack
    status, fc = run_job(r, s3, "synth.tif", overlap=0, conf=0.3)
    bucket, key = status["output_uri"].removeprefix("s3://").split("/", 1)
    before = s3.head_object(Bucket=bucket, Key=key)["LastModified"]
    queued = r.xlen("jobs:in")
    assert submit_job(r, s3, BUCKET, "synth.tif", options=Options(overlap=0, conf=0.3)) \
        == fc["job_id"]
    time.sleep(3)
    assert r.xlen("jobs:in") == queued  # nothing was re-queued
    assert state(r, fc["job_id"]) == "done"
    assert s3.head_object(Bucket=bucket, Key=key)["LastModified"] == before


def test_unparseable_message_is_dead_lettered(stack):
    r, _, _ = stack
    marker = uuid.uuid4().hex
    r.xadd(TILES, {"data": json.dumps({"marker": marker})})
    entry = wait_for(lambda: entries(r, JOBS_DEAD, marker), 30, "dead letter")
    assert "unparseable" in entry[0][1]["reason"]


def test_tile_that_keeps_failing_is_dead_lettered_and_fails_its_job(stack):
    r, _, _ = stack
    job_id = fabricate_job(r, total=1)
    tile = Tile(tenant_id="local", job_id=job_id, idx=0, total=1,
                window=Window(x_off=0, y_off=0, w=64, h=64), tile_bucket="scratch",
                tile_key=f"tenants/local/{job_id}/missing.png", options=Options(model="stub"))
    bus.publish(r, TILES, tile)
    dead = wait_for(lambda: entries(r, JOBS_DEAD, job_id), 60, "dead letter")
    assert int(dead[0][1]["attempts"]) == 4 and "NoSuchKey" in dead[0][1]["reason"]
    assert state(r, job_id) == "error"
    assert "gave up after 3 attempts" in r.hget(f"job:{job_id}:status", "error")


def test_a_stalled_job_is_finalised_as_incomplete_with_the_missing_tiles(stack):
    r, s3, _ = stack
    job_id = fabricate_job(r, total=4, timeout_seconds=3)  # no progress for 3 s = stalled
    for idx in (0, 1, 3):  # tile 2 never arrives
        bus.publish(r, DETECTIONS, detection(job_id, idx, 4))
    wait_for(lambda: state(r, job_id) == "incomplete", 60, "incomplete state")
    uri = r.hget(f"job:{job_id}:status", "output_uri")
    bucket, key = uri.removeprefix("s3://").split("/", 1)
    fc = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    assert fc["incomplete"] is True and fc["missing_tiles"] == [2]
    assert len(fc["features"]) == 3
    assert entries(r, JOBS_DONE, job_id)


def test_finalise_is_exclusive_between_concurrent_workers(stack):
    r, s3, _ = stack
    job_id = fabricate_job(r, total=2)
    for idx in (0, 1):
        d = detection(job_id, idx, 2)
        r.hset(f"job:{job_id}:dets", idx, json.dumps(
            {"window": d.window.model_dump(), "boxes": [b.model_dump() for b in d.boxes]}))
    results = []
    threads = [threading.Thread(target=lambda: results.append(finalise(r, s3, job_id)))
               for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1
    assert len(entries(r, JOBS_DONE, job_id)) == 1
    assert state(r, job_id) == "done"


def test_a_crashed_workers_message_is_reclaimed_and_a_slow_one_is_not_duplicated(stack):
    r, _, _ = stack
    stream, group = f"test:{uuid.uuid4().hex[:8]}", "g"
    msg = Job(tenant_id="local", job_id="j-crash", bucket="b", key="k")
    r.xgroup_create(stream, group, id="0", mkstream=True)
    bus.publish(r, stream, msg)
    # a worker reads the message and dies without acknowledging it
    assert r.xreadgroup(group, "ghost", {stream: ">"}, count=1)

    seen, stop = [], threading.Event()

    def slow_handler(m):
        seen.append(m.job_id)
        time.sleep(3)  # longer than retry_base: only the heartbeat prevents a duplicate

    workers = [threading.Thread(target=bus.consume, args=(r, stream, group, f"w{i}", Job,
                                                          slow_handler),
                                kwargs={"retry_base": 1.0, "stop": stop}) for i in range(2)]
    [w.start() for w in workers]
    try:
        wait_for(lambda: seen, 30, "reclaim of the dead worker's message")
        time.sleep(5)
        assert seen == ["j-crash"]  # handled once despite two live consumers
        wait_for(lambda: r.xpending(stream, group)["pending"] == 0, 10, "ack")
    finally:
        stop.set()
        [w.join(timeout=10) for w in workers]
