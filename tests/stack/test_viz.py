import subprocess

from conftest import RECTS, build_tif, run_job


def test_viz_writes_all_plots_for_real_jobs_and_records_stage_metrics(stack, tmp_path):
    r, s3, _ = stack
    status, fc = run_job(r, s3, "synth.tif", overlap=0)
    # stage timings and counts are recorded for the plots
    assert float(status["detect_seconds"]) > 0 and float(status["finalise_seconds"]) >= 0
    assert int(status["boxes_raw"]) == int(status["boxes_final"]) == len(RECTS)
    assert float(status["slice_seconds"]) >= 0  # measured in-process, so never negative
    assert "submitted_at" in status and "done_at" in status

    # a second job with no detections must plot too
    build_tif(str(tmp_path / "empty.tif"), 1300, 700, [])
    s3.upload_file(str(tmp_path / "empty.tif"), "imagery", "empty.tif")
    _, empty = run_job(r, s3, "empty.tif", overlap=0)
    assert empty["features"] == []

    out = tmp_path / "viz"
    subprocess.run(["geopipe", "viz", fc["job_id"], empty["job_id"], "--out", str(out)], check=True)
    for job_id in (fc["job_id"], empty["job_id"]):
        for name in ("overlay", "tilegrid", "distributions"):
            assert (out / job_id / f"{name}.png").stat().st_size > 2000
    assert (out / "latency.png").stat().st_size > 2000
