"""Benchmark a registered model over a dataset split inside the trainer (ADR-0017).

Faster than one pipeline job per image: each image is cut into the pipeline's own tiles, every
tile goes through the model's ONNX Runtime backend (the same decoder and NMS the detector uses),
the boxes are merged across tile seams by `merge_boxes`, and the result is scored by
`bench.score_model`, the code `geopipe bench` uses. Images and tiles are chosen as the CLI does
(`pick_images`; tiles from `windows`; an all-black tile counts as nodata and is skipped), so a run
here is comparable to a pipeline run. Latency is the model's wall time per image.
"""

import io
import tempfile
import time
import uuid
from collections.abc import Iterable
from pathlib import Path

import numpy as np
from PIL import Image

from . import bus, registry
from .bench import pick_images, score_model, to_coco_detections
from .merge import merge_boxes
from .messages import BenchJob, Options
from .model_backend import Backend, build_backend
from .tiling import windows

DEFAULTS = Options()  # the pipeline's tile size and overlap


def run_inprocess(backend: Backend, images: Iterable[tuple[int, np.ndarray]], conf: float,
                  iou: float, tile: int = DEFAULTS.tile_size,
                  overlap: float = DEFAULTS.overlap) -> dict[int, dict]:
    """image id -> {"features", "latency", "detect_seconds"}, shaped like a pipeline run."""
    runs = {}
    for image_id, img in images:
        height, width = img.shape[:2]
        started = time.monotonic()
        per_tile, detect = {}, 0.0
        for idx, win in enumerate(windows(width, height, tile, overlap)):
            crop = img[win.y_off:win.y_off + win.h, win.x_off:win.x_off + win.w]
            if not crop.any():  # nodata: the slicer does not send these
                continue
            t = time.monotonic()
            boxes = backend(crop, conf)
            detect += time.monotonic() - t
            per_tile[idx] = {"window": win.model_dump(), "boxes": [b.model_dump() for b in boxes]}
        merged = merge_boxes(per_tile, width, height, overlap, iou)
        runs[image_id] = {
            "features": [{"properties": {
                "class_name": b["class_name"], "class_id": b["class_id"], "score": b["score"],
                "pixel_bbox": [b["x1"], b["y1"], b["x2"], b["y2"]]}} for b in merged],
            "latency": time.monotonic() - started, "detect_seconds": detect}
    return runs


def handle_bench(r, s3, job: BenchJob, build=build_backend, device: str = "cpu") -> None:
    """Score `job.model_id` on a split and store the run (manifest last), like `geopipe bench`."""
    bus.set_status(r, job.job_id, "running", tenant_id=job.tenant_id, kind="bench", done=0,
                   total=0)
    model = registry.find_model(s3, job.bucket, job.tenant_id, job.model_id)
    if model is None or model[1].state != "ready":
        raise bus.PermanentError("unknown model")
    scope, manifest = model
    if manifest.backend is not None:
        raise bus.PermanentError(
            "this built-in model has no ONNX file in the registry; its benchmarks come from "
            "`make bench-spacenet`")
    dataset = registry.find(s3, job.bucket, job.tenant_id, job.dataset_id)
    if dataset is None or dataset[1].state != "ready":
        raise bus.PermanentError("unknown dataset, or it is not ready")
    ds_scope = dataset[0]
    coco = registry.read_annotations(s3, job.bucket, ds_scope, job.dataset_id, job.split)
    if coco is None:
        raise bus.PermanentError(f"the dataset has no {job.split!r} split")
    infos = pick_images(coco, job.limit or len(coco["images"]))
    if not infos:
        raise bus.PermanentError("the split has no annotated images to score")
    name_to_id = {c["name"]: c["id"] for c in coco["categories"]}
    if not set(manifest.classes) & set(name_to_id):
        raise bus.PermanentError(
            f"the model's classes {manifest.classes} share none with the dataset's "
            f"{sorted(name_to_id)}")
    base = registry.prefix(ds_scope, job.dataset_id)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.onnx"
        s3.download_file(job.bucket, f"{registry.model_prefix(scope, job.model_id)}/model.onnx",
                         str(path))
        try:
            backend = build(device, manifest, str(path))
        except Exception as e:
            raise bus.PermanentError(f"could not load the model: {e}") from e

        def images():
            for n, info in enumerate(infos):
                body = s3.get_object(Bucket=job.bucket,
                                     Key=f"{base}/{job.split}/{info['file_name']}")["Body"].read()
                yield info["id"], np.array(Image.open(io.BytesIO(body)).convert("RGB"))
                bus.set_status(r, job.job_id, "running", done=n + 1, total=len(infos))

        runs = run_inprocess(backend, images(), job.conf, job.iou)
    ids = [i["id"] for i in infos]
    scored = score_model(coco, name_to_id, runs, ids)
    detections = [d for i in ids for d in to_coco_detections(i, runs[i]["features"], name_to_id)]
    run = registry.RunManifest(
        id=f"run-{uuid.uuid4().hex[:12]}", model_id=job.model_id, dataset_id=job.dataset_id,
        split=job.split, scorer="in-process", conf=job.conf, iou=job.iou, created_at=time.time(),
        metrics={k: float(scored[k]) for k in registry.METRIC_KEYS})
    registry.write_run(s3, r, job.bucket, run, scored, detections, scope=job.tenant_id)
    bus.set_status(r, job.job_id, "done", tenant_id=job.tenant_id, kind="bench", run_id=run.id,
                   done=len(ids), total=len(ids))
