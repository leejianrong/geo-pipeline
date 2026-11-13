import json
from pathlib import Path

from geopipe.bench import run_benchmark

FIXTURE = Path(__file__).parents[1] / "fixtures" / "coco_mini.json"


def run(stack, out):
    r, s3, _ = stack
    coco = json.loads(FIXTURE.read_text())
    images = Path("/data/cache/coco") if Path("/data").is_dir() else Path("/tmp/coco")
    return run_benchmark(r, s3, coco, ["rfdetr", "yolox"], images, out, limit=5, download=True)


def test_benchmark_scores_both_models_through_the_pipeline_and_is_deterministic(stack, tmp_path):
    first = run(stack, tmp_path / "a")
    for name in ("report.md", "results.json", "pr_curves.png", "latency_iou.png"):
        assert (tmp_path / "a" / name).stat().st_size > 500, name
    # Sanity floors on 5 easy val images, well below what both models score.
    assert first["rfdetr"]["map50"] > 0.5
    assert first["yolox"]["map50"] > 0.3
    assert first["rfdetr"]["detections"] > 0 and first["yolox"]["detections"] > 0

    second = run(stack, tmp_path / "b")  # a fresh tenant, so the jobs really run again
    for model in first:
        for key in ("map", "map50", "ar100", "tp", "fp", "fn", "detections"):
            assert second[model][key] == first[model][key], (model, key)
