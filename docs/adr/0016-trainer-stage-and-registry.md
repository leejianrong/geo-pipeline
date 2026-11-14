# ADR-0016: A trainer stage on the existing bus, and manifests in the object store

- Status: Accepted
- Date: 2026-11-15
- Deciders: Jian, Claude

## Context

Training today is a `docker run` started by hand (`make train-yolox`, `make train-rfdetr`), and benchmarking is `geopipe bench` driving jobs through the pipeline. The studio needs both as jobs started from a browser, and it needs to know which datasets, models and runs exist. Postgres job records are an open card (GEO-41).

## Decision

- **A `trainer` stage** (`stages/trainer/`) consumes three streams, `ingest` (validate and register an uploaded dataset), `train` and `bench`, with the same consumer-group, retry and dead-letter rules as the other stages (ADR-0011). The image has two variants, like the detector (ADR-0012): a small default (ONNX Runtime, pycocotools, onnx, Pillow; no torch) that handles `ingest` and `bench` and is what CI builds and runs, and a GPU variant from `train/Dockerfile` that also handles `train`. A `train` message reaching the small variant fails at once with a message saying which image is needed. One job at a time per worker. At most one active training per tenant; others wait in the stream.
- **No GPU, no training.** A `train` job on a host without a visible GPU fails at once with an explicit message (as ADR-0012 does for the detector). There is no CPU fallback: it would take hours. Benchmarks run on CPU or GPU.
- **Messages** (`geopipe/messages.py`, with `tenant_id` and `schema_version`): `IngestJob(dataset_id, upload_key)`, `TrainJob(run_id, dataset_id, base, preset, epochs)` and `BenchJob(run_id, model_id, dataset_id, split)`. Presets map to a base model and recipe: `fast` = YOLOX-tiny 416, `balanced` = YOLOX-s 640, `accurate` = RF-DETR Nano, `best` = RF-DETR Small, with the recipes of ADR-0014's benchmarks. Raw hyperparameters are not exposed. Every training run ends by exporting an ONNX model (`export_yolox_onnx.py`, `export_rfdetr_onnx.py`) and registering it, so a trained model is a registry model like an uploaded one (ADR-0017).
- **Remote GPUs later:** the same messages can be consumed by a worker on a rented GPU (see the runpod-jobs skill). That is out of this milestone.
- **Registry = JSON manifests in the object store**, written by the stage that creates the thing, plus a Valkey sorted set per tenant and kind for listing:
  - `tenants/<tid>/datasets/<id>/manifest.json` (+ `train|valid|test/_annotations.coco.json` with the images beside them, the layout `spacenet-prepare` writes and both trainers read, and `thumbs/`): name, format `coco`, classes, split counts, image count, licence and attribution text.
  - `tenants/<tid>/models/<id>/manifest.json` + `model.onnx`: name, architecture and decoder (`yolox` or `rfdetr`), input size, classes, trained-on dataset, run id, the bar-relevant metrics when known.
  - `tenants/<tid>/runs/<id>/manifest.json`, `results.json`, `predictions.json`: kind (train or bench), state, progress, metrics, and per-image predictions for the Explore view.
  - `shared/...` holds the built-in SpaceNet dataset and its fine-tuned models, readable by every tenant, written by `geopipe register-builtin`. Tenant keys can never address `shared/`.
- A manifest is written last and carries a `state` (`ingesting`, `ready`, `failed`), so a half-written dataset never shows as ready. Listings show only `ready` and the owner's `failed` ones with their report.
- Every manifest carries `schema_version`. When job records move to Postgres (GEO-41), the manifests are read once and migrated; nothing else changes.

## Alternatives considered

| Option | Why not |
|---|---|
| Postgres for the registry now | A new service and a migration story before there is demand for queries; the manifests migrate later |
| Run training inside the API or frontend process | Blocks the web server, no retry or isolation, and the GPU image is too large for them |
| CPU fallback for training | Hours per run; looks like a hang |
| Raw hyperparameters in the UI | Easy to produce bad runs; presets match what we benchmarked in ADR-0014 |

## Consequences

- A new stage and image to build in CI (the train image is large; CI builds it but does not train).
- Listing and search are basic until the Postgres move.
- Deleting a dataset must also delete its derived runs' references; slice S7 covers delete and quotas.
