import numpy as np
import pytest

from geopipe.viz import JobData, plot_latency, write_plots


def make_job(job_id, features):
    overview = np.full((50, 100, 3), 90, np.uint8)
    valid = np.ones((50, 100), bool)
    valid[:, 60:] = False  # right part of the raster has no data
    meta = {"width": 2000, "height": 1000, "tile_size": 640, "overlap": 0.2, "model": "stub"}
    status = {"state": "done", "total": "6", "slice_seconds": "1", "detect_seconds": "2",
              "finalise_seconds": "0.1", "submitted_at": "0", "done_at": "4"}
    return JobData(job_id, status, meta, features, overview, valid)


def feature(name, score, box):
    return {"properties": {"class_name": name, "score": score, "pixel_bbox": box}}


@pytest.mark.parametrize("features", [
    [feature("car", 0.9, [100, 100, 160, 140]), feature("boat", 0.5, [900, 300, 980, 360])],
    [],
])
def test_every_plot_renders_with_and_without_detections(tmp_path, features):
    jobs = [make_job("aaaaaaaa11", features), make_job("bbbbbbbb22", features)]
    out = write_plots(jobs, tmp_path)
    for job in jobs:
        for name in ("overlay", "tilegrid", "distributions"):
            assert (out / job.job_id / f"{name}.png").stat().st_size > 2000
    assert (out / "latency.png").stat().st_size > 2000


def test_latency_plot_handles_jobs_without_a_submit_time(tmp_path):
    job = make_job("cccccccc33", [])
    del job.status["submitted_at"]
    plot_latency([job], tmp_path / "l.png")
    assert (tmp_path / "l.png").exists()
