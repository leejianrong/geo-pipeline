"""Multi-GB rasters: slicer memory and COG vs non-COG timing. Run with `make bench-large`."""

import json
import os

import pytest
from conftest import build_tif, run_job
from osgeo import gdal

SIDE = 26000  # 26000 x 26000 x 3 bytes = 2 GB decoded
RECTS = [(1000 + i * 4000, 1000 + i * 4000, 100, 80) for i in range(5)]
results: dict = {}


@pytest.mark.slow
@pytest.mark.parametrize("layout", ["tiled", "cog"])
def test_two_gb_raster_stays_within_memory_and_is_correct(stack, tmp_path, layout):
    r, s3, _ = stack
    src = str(tmp_path / "big.tif")
    build_tif(src, SIDE, SIDE, RECTS)
    path = src
    if layout == "cog":
        path = str(tmp_path / "big_cog.tif")
        gdal.Translate(path, src, format="COG", creationOptions=["COMPRESS=DEFLATE"])
    key = f"big-{layout}.tif"
    s3.upload_file(path, "imagery", key)

    status, fc = run_job(r, s3, key, timeout=900, overlap=0)
    assert len(fc["features"]) == len(RECTS)
    results[layout] = {"file_mb": os.path.getsize(path) // 2**20, "tiles": int(status["total"]),
                       "slice_seconds": float(status["slice_seconds"]),
                       "slicer_peak_rss_mb": int(status["slicer_peak_rss_mb"])}
    print(f"\n{layout}: {results[layout]}")
    assert results[layout]["slicer_peak_rss_mb"] < 1500  # lifetime peak of the slicer process

    if os.path.isdir("/data"):
        os.makedirs("/data/out", exist_ok=True)
        with open("/data/out/large_raster.json", "w") as f:
            json.dump(results, f, indent=2)
