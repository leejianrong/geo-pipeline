"""Dataset registry (ADR-0016): manifests in the object store, listed through Valkey.

A dataset lives under `shared/datasets/<id>/` (built in, readable by every tenant) or
`tenants/<tid>/datasets/<id>/`. Each split directory holds the images and a COCO
`_annotations.coco.json`, the layout `spacenet-prepare` writes and both trainers read. The
manifest is written last, so a half-registered dataset never shows as ready.
"""

import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

SHARED = "shared"
SPLITS = ("train", "valid", "test")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
FILE_RE = re.compile(r"^[\w][\w.-]*$")  # one path segment, no ".."
THUMB_PX = 256
SPACENET_ATTRIBUTION = (
    "SpaceNet 2 AOI 5 (Khartoum): SpaceNet on Amazon Web Services (AWS), \"Datasets\", The "
    "SpaceNet Catalog, https://spacenet.ai/datasets/, licensed CC BY-SA 4.0.")


class DatasetManifest(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    name: str
    format: Literal["coco"] = "coco"
    classes: list[str]
    splits: dict[str, int]  # images per split
    boxes: int
    licence: str = Field(min_length=1)
    attribution: str
    state: Literal["ingesting", "ready", "failed"]
    created_at: float


def prefix(scope: str, dataset_id: str) -> str:
    """Key prefix of a dataset: `scope` is a tenant id or SHARED."""
    base = SHARED if scope == SHARED else f"tenants/{scope}"
    return f"{base}/datasets/{dataset_id}"


def index_key(scope: str) -> str:
    return f"datasets:{SHARED}" if scope == SHARED else f"tenant:{scope}:datasets"


def _get_json(s3, bucket: str, key: str) -> dict | None:
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise


def find(s3, bucket: str, tenant: str, dataset_id: str) -> tuple[str, DatasetManifest] | None:
    """The tenant's own dataset first, then a shared one; None if neither exists."""
    if not ID_RE.match(dataset_id):
        return None
    for scope in (tenant, SHARED):
        raw = _get_json(s3, bucket, f"{prefix(scope, dataset_id)}/manifest.json")
        if raw:
            return scope, DatasetManifest.model_validate(raw)
    return None


def list_datasets(r, s3, bucket: str, tenant: str) -> list[tuple[str, DatasetManifest]]:
    """Ready shared datasets, then the tenant's own (their failed ones too, for the report)."""
    found = []
    for scope in (SHARED, tenant):
        for dataset_id in r.zrange(index_key(scope), 0, -1):
            hit = find(s3, bucket, tenant, dataset_id)
            if hit and hit[0] == scope and (hit[1].state == "ready" or scope == tenant):
                found.append(hit)
    return found


def read_annotations(s3, bucket: str, scope: str, dataset_id: str, split: str) -> dict | None:
    return _get_json(s3, bucket, f"{prefix(scope, dataset_id)}/{split}/_annotations.coco.json")


def thumbnail(png_or_jpeg: bytes) -> bytes:
    from PIL import Image

    img = Image.open(io.BytesIO(png_or_jpeg)).convert("RGB")
    img.thumbnail((THUMB_PX, THUMB_PX))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80)
    return out.getvalue()


def register_dataset(s3, r, bucket: str, root: Path, dataset_id: str, *, scope: str = SHARED,
                     name: str, licence: str, attribution: str, now=time.time) -> DatasetManifest:
    """Upload `root/{train,valid,test}/` with thumbnails, then write the manifest (idempotent)."""
    if not ID_RE.match(dataset_id):
        raise ValueError(f"dataset id must match {ID_RE.pattern}")
    base = prefix(scope, dataset_id)
    splits, boxes, classes = {}, 0, []
    for split in SPLITS:
        ann_path = root / split / "_annotations.coco.json"
        if not ann_path.exists():
            continue
        coco = json.loads(ann_path.read_text())
        classes = classes or [c["name"] for c in sorted(coco["categories"], key=lambda c: c["id"])]
        splits[split], boxes = len(coco["images"]), boxes + len(coco["annotations"])

        def upload(file_name: str, split=split) -> None:
            data = (root / split / file_name).read_bytes()
            s3.put_object(Bucket=bucket, Key=f"{base}/{split}/{file_name}", Body=data)
            s3.put_object(Bucket=bucket, Key=f"{base}/thumbs/{split}/{Path(file_name).stem}.jpg",
                          Body=thumbnail(data), ContentType="image/jpeg")

        with ThreadPoolExecutor(8) as pool:
            list(pool.map(upload, [i["file_name"] for i in coco["images"]]))
        s3.put_object(Bucket=bucket, Key=f"{base}/{split}/_annotations.coco.json",
                      Body=ann_path.read_bytes(), ContentType="application/json")
    if not splits:
        raise ValueError(f"no split with _annotations.coco.json under {root}")
    manifest = DatasetManifest(id=dataset_id, name=name, classes=classes, splits=splits,
                               boxes=boxes, licence=licence, attribution=attribution,
                               state="ready", created_at=now())
    s3.put_object(Bucket=bucket, Key=f"{base}/manifest.json", Body=manifest.model_dump_json(),
                  ContentType="application/json")
    r.zadd(index_key(scope), {dataset_id: manifest.created_at})
    return manifest


class ModelManifest(BaseModel):
    """A model the studio can show. `backend` is the detector backend that runs a built-in one;
    uploaded models (later slices) have none and run from their own ONNX file."""

    schema_version: Literal[1] = 1
    id: str
    name: str
    decoder: Literal["yolox", "rfdetr"]
    input_size: int
    classes: list[str]
    trained_on: str | None = None  # dataset id
    backend: str | None = None
    licence: str = Field(min_length=1)
    state: Literal["ready", "failed"] = "ready"
    created_at: float


# Built-in models: the SpaceNet fine-tunes of ADR-0014 (backend name = id).
BUILTIN_MODELS = [
    ("yolox-tiny-buildings", "YOLOX-tiny 416", "yolox", 416),
    ("yolox-tiny-buildings-int8", "YOLOX-tiny 416, INT8", "yolox", 416),
    ("yolox-buildings", "YOLOX-s 640", "yolox", 640),
    ("yolox-buildings-int8", "YOLOX-s 640, INT8", "yolox", 640),
    ("rfdetr-nano-buildings", "RF-DETR Nano", "rfdetr", 384),
    ("rfdetr-buildings", "RF-DETR Small", "rfdetr", 512),
]


def model_prefix(scope: str, model_id: str) -> str:
    base = SHARED if scope == SHARED else f"tenants/{scope}"
    return f"{base}/models/{model_id}"


def register_model(s3, r, bucket: str, model: ModelManifest, scope: str = SHARED) -> None:
    if not ID_RE.match(model.id):
        raise ValueError(f"model id must match {ID_RE.pattern}")
    s3.put_object(Bucket=bucket, Key=f"{model_prefix(scope, model.id)}/manifest.json",
                  Body=model.model_dump_json(), ContentType="application/json")
    r.zadd(f"models:{SHARED}" if scope == SHARED else f"tenant:{scope}:models",
           {model.id: model.created_at})


def register_builtin_models(s3, r, bucket: str, dataset_id: str, now=time.time) -> list[str]:
    for model_id, name, decoder, size in BUILTIN_MODELS:
        register_model(s3, r, bucket, ModelManifest(
            id=model_id, name=name, decoder=decoder, input_size=size, classes=["building"],
            trained_on=dataset_id, backend=model_id, licence="CC BY-SA 4.0", created_at=now()))
    return [m[0] for m in BUILTIN_MODELS]


class RunManifest(BaseModel):
    """One benchmark of one model on one split of a dataset."""

    schema_version: Literal[1] = 1
    id: str
    kind: Literal["bench"] = "bench"
    state: Literal["done", "failed"] = "done"
    model_id: str
    dataset_id: str
    split: str
    scorer: Literal["pipeline", "in-process"]  # how the boxes were produced
    conf: float
    iou: float
    metrics: dict[str, float]
    created_at: float


METRIC_KEYS = ("map", "map50", "ar100", "precision", "recall", "f1", "tp", "fp", "fn",
               "detections", "median_latency", "mean_detect_seconds")


def run_prefix(scope: str, run_id: str) -> str:
    base = SHARED if scope == SHARED else f"tenants/{scope}"
    return f"{base}/runs/{run_id}"


def write_run(s3, r, bucket: str, run: RunManifest, scored: dict, predictions: list[dict],
              scope: str = SHARED) -> None:
    """Save a run: `predictions.json` (COCO results format, so it can be rescored),
    `results.json` (the metrics and the precision-recall curve), then the manifest."""
    base = run_prefix(scope, run.id)
    results = {**run.metrics, "pr_recall": scored["pr_recall"],
               "pr_precision": scored["pr_precision"]}
    s3.put_object(Bucket=bucket, Key=f"{base}/predictions.json", Body=json.dumps(predictions),
                  ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=f"{base}/results.json", Body=json.dumps(results),
                  ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=f"{base}/manifest.json", Body=run.model_dump_json(),
                  ContentType="application/json")
    r.zadd(f"runs:{SHARED}" if scope == SHARED else f"tenant:{scope}:runs",
           {run.id: run.created_at})
