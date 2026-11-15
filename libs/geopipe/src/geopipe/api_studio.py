"""Studio routes (ADR-0015): datasets, models and runs. Read-only; images come as presigned URLs.

A tenant sees its own datasets and the shared ones, and nothing else. Ids and file names are
checked against strict patterns because they become object keys.
"""

import json
import re
import uuid
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import bus, ingest, model_ingest, registry, store, train
from .jobs import submit_bench, submit_ingest, submit_model_ingest, submit_train
from .presets import MAX_EPOCHS, PRESETS


class DatasetRequest(BaseModel):
    """What the owner says about an uploaded zip."""

    upload_key: str
    name: str = Field(min_length=1, max_length=80)
    licence: str = Field(min_length=1, max_length=200)
    attribution: str = Field(max_length=2000)


class ModelRequest(BaseModel):
    """What the owner says about an uploaded ONNX file."""

    upload_key: str
    name: str = Field(min_length=1, max_length=80)
    decoder: Literal["yolox", "rfdetr"]
    classes: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=100)
    licence: str = Field(min_length=1, max_length=200)


class BenchRequest(BaseModel):
    model_id: str
    dataset_id: str
    split: str
    limit: int | None = Field(default=None, ge=1, le=20_000)


class TrainRequest(BaseModel):
    dataset_id: str
    preset: str
    epochs: int | None = Field(default=None, ge=1, le=MAX_EPOCHS)


def thumb_key(split: str, file_name: str) -> str:
    return f"thumbs/{split}/{file_name.rsplit('.', 1)[0]}.jpg"


def add_dataset_routes(app: FastAPI, r, s3, public_s3, bucket: str, tenant: Callable) -> None:
    def manifest(dataset_id: str, tid: str) -> tuple[str, registry.DatasetManifest]:
        hit = registry.find(s3, bucket, tid, dataset_id)
        if not hit or (hit[1].state != "ready" and hit[0] != tid):
            raise HTTPException(404, "unknown dataset")  # same answer for another tenant's
        return hit

    def annotations(scope: str, dataset_id: str, split: str) -> dict:
        coco = registry.read_annotations(s3, bucket, scope, dataset_id, split) \
            if split in registry.SPLITS else None
        if coco is None:
            raise HTTPException(404, "unknown split")
        return coco

    def url(scope: str, dataset_id: str, rest: str) -> str:
        return store.presign_get(public_s3, bucket, f"{registry.prefix(scope, dataset_id)}/{rest}")

    @app.post("/datasets", status_code=202)
    def create_dataset(req: DatasetRequest, tid: str = Depends(tenant)) -> dict:
        """Queue an uploaded zip for validation; progress is `GET /jobs/{job_id}`, the result is
        the dataset's manifest (`ready`, or `failed` with its report)."""
        if ".." in req.upload_key or not req.upload_key.startswith(ingest.upload_prefix(tid)):
            raise HTTPException(403, "key is not one of your dataset uploads")
        slug = re.sub(r"[^a-z0-9]+", "-", req.name.lower()).strip("-")[:40] or "dataset"
        dataset_id = f"{slug}-{uuid.uuid4().hex[:6]}"
        job_id = submit_ingest(r, bucket, tid, req.upload_key, dataset_id, req.name, req.licence,
                               req.attribution)
        return {"dataset_id": dataset_id, "job_id": job_id}

    @app.get("/datasets")
    def list_datasets(tid: str = Depends(tenant)) -> list[dict]:
        return [{**m.model_dump(), "scope": "shared" if scope == registry.SHARED else "own"}
                for scope, m in registry.list_datasets(r, s3, bucket, tid)]

    @app.get("/datasets/{dataset_id}")
    def get_dataset(dataset_id: str, tid: str = Depends(tenant)) -> dict:
        scope, m = manifest(dataset_id, tid)
        return {**m.model_dump(), "scope": "shared" if scope == registry.SHARED else "own"}

    @app.delete("/datasets/{dataset_id}")
    def delete_dataset(dataset_id: str, tid: str = Depends(tenant)) -> dict:
        """Delete one of your datasets and the stored runs scored on it. Built-in datasets are
        read-only, and one that a training run or benchmark is using cannot go."""
        hit = registry.find(s3, bucket, tid, dataset_id)
        if hit is None:
            raise HTTPException(404, "unknown dataset")
        if hit[0] == registry.SHARED:
            raise HTTPException(403, "built-in datasets cannot be deleted")
        if busy := train.in_use(r, s3, bucket, tid, dataset_id=dataset_id):
            raise HTTPException(409, f"in use by {', '.join(busy)}; wait for it or cancel it")
        runs = registry.runs_of(r, s3, bucket, tid, dataset_id=dataset_id)
        for run in runs:
            registry.delete_run(s3, r, bucket, tid, run.id)
        registry.delete_dataset(s3, r, bucket, tid, dataset_id)
        return {"deleted": dataset_id, "runs_deleted": len(runs)}

    @app.get("/datasets/{dataset_id}/annotations/{split}")
    def get_annotations(dataset_id: str, split: str, tid: str = Depends(tenant)) -> dict:
        scope, _ = manifest(dataset_id, tid)
        return annotations(scope, dataset_id, split)

    @app.get("/datasets/{dataset_id}/images/{split}")
    def list_images(dataset_id: str, split: str, offset: int = 0, limit: int = 60,
                    tid: str = Depends(tenant)) -> list[dict]:
        """One page of images with thumbnail URLs and the number of ground-truth boxes."""
        scope, _ = manifest(dataset_id, tid)
        coco = annotations(scope, dataset_id, split)
        counts: dict[int, int] = {}
        for a in coco["annotations"]:
            counts[a["image_id"]] = counts.get(a["image_id"], 0) + 1
        page = coco["images"][max(0, offset):max(0, offset) + max(1, min(limit, 200))]
        return [{"file": i["file_name"], "width": i["width"], "height": i["height"],
                 "boxes": counts.get(i["id"], 0),
                 "thumb_url": url(scope, dataset_id, thumb_key(split, i["file_name"]))}
                for i in page]

    @app.get("/datasets/{dataset_id}/images/{split}/{file}")
    def get_image(dataset_id: str, split: str, file: str, tid: str = Depends(tenant)) -> dict:
        """One image: its URL, size and ground-truth boxes as pixel [x, y, w, h]."""
        scope, _ = manifest(dataset_id, tid)
        if not registry.FILE_RE.match(file):
            raise HTTPException(404, "unknown image")
        coco = annotations(scope, dataset_id, split)
        info = next((i for i in coco["images"] if i["file_name"] == file), None)
        if info is None:
            raise HTTPException(404, "unknown image")
        return {"url": url(scope, dataset_id, f"{split}/{file}"), "width": info["width"],
                "height": info["height"],
                "boxes": [a["bbox"] for a in coco["annotations"] if a["image_id"] == info["id"]]}


def add_model_routes(app: FastAPI, r, s3, bucket: str, tenant: Callable) -> None:
    """Models and benchmark runs. A tenant sees its own and the shared ones; another tenant's id
    answers 404, the same as an unknown one."""

    def tag(scope: str, m) -> dict:
        return {**m.model_dump(), "scope": "shared" if scope == registry.SHARED else "own"}

    def run_of(run_id: str, tid: str) -> tuple[str, registry.RunManifest]:
        hit = registry.find_run(s3, bucket, tid, run_id)
        if not hit:
            raise HTTPException(404, "unknown run")
        return hit

    def predictions(scope: str, run: registry.RunManifest, run_id: str) -> dict[str, list[dict]]:
        """The run's boxes grouped by image file: pixel [x, y, w, h] and score."""
        base = registry.run_prefix(scope, run_id)
        raw = registry._get_json(s3, bucket, f"{base}/predictions.json")
        ds = registry.find(s3, bucket, scope, run.dataset_id)
        coco = (registry.read_annotations(s3, bucket, ds[0], run.dataset_id, run.split)
                if ds else None)
        if raw is None or coco is None:
            raise HTTPException(404, "predictions are not available")
        files = {i["id"]: i["file_name"] for i in coco["images"]}
        out: dict[str, list[dict]] = {f: [] for f in files.values()}
        for p in raw:
            if p["image_id"] in files:
                out[files[p["image_id"]]].append({"bbox": p["bbox"], "score": p["score"]})
        return out

    @app.post("/models", status_code=202)
    def create_model(req: ModelRequest, tid: str = Depends(tenant)) -> dict:
        """Queue an uploaded ONNX file for validation; progress is `GET /jobs/{job_id}`, the result
        is the model's manifest (`ready`, or `failed` with its report)."""
        if ".." in req.upload_key or not req.upload_key.startswith(model_ingest.upload_prefix(tid)):
            raise HTTPException(403, "key is not one of your model uploads")
        slug = re.sub(r"[^a-z0-9]+", "-", req.name.lower()).strip("-")[:40] or "model"
        model_id = f"{slug}-{uuid.uuid4().hex[:6]}"
        job_id = submit_model_ingest(r, bucket, tid, req.upload_key, model_id, req.name,
                                     req.decoder, req.classes, req.licence)
        return {"model_id": model_id, "job_id": job_id}

    @app.get("/models")
    def list_models(tid: str = Depends(tenant)) -> list[dict]:
        return [tag(scope, m) for scope, m in registry.list_models(r, s3, bucket, tid)]

    @app.delete("/models/{model_id}")
    def delete_model(model_id: str, tid: str = Depends(tenant)) -> dict:
        """Delete one of your models and the stored runs scored for it. Built-in models are
        read-only, and one that a benchmark is using cannot go."""
        hit = registry.find_model(s3, bucket, tid, model_id)
        if hit is None:
            raise HTTPException(404, "unknown model")
        if hit[0] == registry.SHARED:
            raise HTTPException(403, "built-in models cannot be deleted")
        if busy := train.in_use(r, s3, bucket, tid, model_id=model_id):
            raise HTTPException(409, f"in use by {', '.join(busy)}; wait for it")
        runs = registry.runs_of(r, s3, bucket, tid, model_id=model_id)
        for run in runs:
            registry.delete_run(s3, r, bucket, tid, run.id)
        registry.delete_model(s3, r, bucket, tid, model_id)
        return {"deleted": model_id, "runs_deleted": len(runs)}

    @app.get("/models/{model_id}")
    def get_model(model_id: str, tid: str = Depends(tenant)) -> dict:
        hit = registry.find_model(s3, bucket, tid, model_id)
        if not hit:
            raise HTTPException(404, "unknown model")
        return tag(*hit)

    @app.post("/bench/runs", status_code=202)
    def start_bench(req: BenchRequest, tid: str = Depends(tenant)) -> dict:
        """Score a registered model on a split in the trainer. Progress is `GET /jobs/{job_id}`;
        the finished run (`run_id` in that status) is an ordinary run, listed by `GET /runs`."""
        try:
            job_id = submit_bench(r, s3, bucket, tid, req.model_id, req.dataset_id, req.split,
                                  req.limit)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"job_id": job_id}

    @app.get("/runs")
    def list_runs(model_id: str | None = None, dataset_id: str | None = None,
                  tid: str = Depends(tenant)) -> list[dict]:
        return [tag(scope, m) for scope, m in registry.list_runs(r, s3, bucket, tid)
                if model_id in (None, m.model_id) and dataset_id in (None, m.dataset_id)]

    @app.delete("/runs/{run_id}")
    def delete_run(run_id: str, tid: str = Depends(tenant)) -> dict:
        scope, _ = run_of(run_id, tid)
        if scope == registry.SHARED:
            raise HTTPException(403, "built-in runs cannot be deleted")
        registry.delete_run(s3, r, bucket, tid, run_id)
        return {"deleted": run_id}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, tid: str = Depends(tenant)) -> dict:
        """The manifest plus the precision-recall curve."""
        scope, run = run_of(run_id, tid)
        results = registry._get_json(
            s3, bucket, f"{registry.run_prefix(scope, run_id)}/results.json") or {}
        return {**tag(scope, run), "pr_recall": results.get("pr_recall", []),
                "pr_precision": results.get("pr_precision", [])}

    @app.get("/runs/{run_id}/predictions")
    def get_predictions(run_id: str, tid: str = Depends(tenant)) -> dict[str, list[dict]]:
        scope, run = run_of(run_id, tid)
        return predictions(scope, run, run_id)

    @app.get("/runs/{run_id}/predictions/{file}")
    def get_image_predictions(run_id: str, file: str, min_score: float = 0.0,
                              tid: str = Depends(tenant)) -> list[dict]:
        scope, run = run_of(run_id, tid)
        boxes = predictions(scope, run, run_id).get(file)
        if boxes is None:
            raise HTTPException(404, "unknown image")
        return [b for b in boxes if b["score"] >= min_score]


def add_train_routes(app: FastAPI, r, s3, bucket: str, tenant: Callable) -> None:
    """Fine-tuning runs (ADR-0016). Always the caller's own; another tenant's id is a 404."""

    def live(run: registry.TrainRunManifest, history: bool = False) -> dict:
        """The manifest merged with the live status (epoch, loss, validation AP, and the state
        while the run is unfinished); `history` adds the per-epoch loss and AP for the chart."""
        out = run.model_dump()
        status = r.hgetall(f"job:{run.id}:status")  # kept for days after the run ends
        out |= {k: float(status[k]) if k in ("loss", "val_ap") else status[k]
                for k in ("epoch", "loss", "val_ap") if k in status}
        out["epoch"] = int(out.get("epoch", 0))
        if status.get("state") == "waiting":
            out["waiting_for"] = status.get("waiting_for", "")
        if run.state == "queued":  # the manifest is final once it says anything else
            out["state"] = status.get("state", "queued")
        if history:
            out["history"] = sorted((json.loads(v) for v in r.hgetall(
                train.history_key(run.id)).values()), key=lambda e: e["epoch"])
        return out

    @app.get("/train/presets")
    def presets() -> list[dict]:
        return [{"name": n, "label": p.label, "epochs": p.epochs} for n, p in PRESETS.items()]

    @app.post("/train/runs", status_code=202)
    def start_run(req: TrainRequest, tid: str = Depends(tenant)) -> dict:
        try:
            return {"run_id": submit_train(r, s3, bucket, tid, req.dataset_id, req.preset,
                                           req.epochs)}
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/train/runs")
    def list_runs(tid: str = Depends(tenant)) -> list[dict]:
        return [live(run) for run in registry.list_train_runs(r, s3, bucket, tid)]

    @app.get("/train/runs/{run_id}")
    def get_run(run_id: str, tid: str = Depends(tenant)) -> dict:
        run = registry.find_train_run(s3, bucket, tid, run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        return live(run, history=True)

    @app.post("/train/runs/{run_id}/cancel", status_code=202)
    def cancel_run(run_id: str, tid: str = Depends(tenant)) -> dict:
        """Stop a queued, waiting or running run; the trainer does it within a few seconds."""
        run = registry.find_train_run(s3, bucket, tid, run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        if run.state != "queued":
            raise HTTPException(409, f"the run already ended ({run.state})")
        train.request_cancel(r, run_id)
        return {"run_id": run_id, "cancelling": True}

    @app.delete("/train/runs/{run_id}")
    def delete_train_run(run_id: str, tid: str = Depends(tenant)) -> dict:
        """Forget a finished training run (the model it made stays). Cancel a running one first."""
        run = registry.find_train_run(s3, bucket, tid, run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        if run.state == "queued" and r.hget(f"job:{run_id}:status", "state") not in bus.TERMINAL:
            raise HTTPException(409, "the run is still going; cancel it first")
        registry.delete_run(s3, r, bucket, tid, run_id)
        return {"deleted": run_id}
