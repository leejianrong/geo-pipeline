# geo-pipeline: Plan

Status: draft v2 · Milestone: MVP (local compose), designed to grow into a hosted service

## Problem

Overhead imagery arrives as GeoTIFFs far larger than any detector's input size. Running YOLOX or RF-DETR on them means slicing, batching inference, merging per-tile boxes, and converting pixels back to map coordinates. Doing that by hand is slow and easy to get subtly wrong (off-by-a-tile coordinates, duplicate boxes along seams).

A second problem comes first: there is no ready-made container image with GDAL, Python 3.12 or 3.13, and numpy 2.x together. The official OSGeo images pin whatever Python their Ubuntu base ships.

## Solution

1. **Base images** `geo-gdal:py3.12` and `geo-gdal:py3.13` (GDAL + numpy 2.x), built and smoke-tested by us.
2. **A pipeline** in three worker containers (slice, detect, postprocess). Submit a message naming a `.tif` in S3 and get a georeferenced `.geojson` of detections back, plus a "done" event.
3. **A service-shaped architecture.** MVP runs locally with `docker compose up`. Every boundary (job message, storage layout, status record) already carries a tenant and job identity, so a hosted API and frontend can be added without reworking the workers (ADR-0008).

## Users and actors

1. **ML/dev users** (primary now): submit imagery, swap models, read GeoJSON in QGIS or code.
2. **Future end users** of a hosted service via a web frontend (not built in MVP; shapes the architecture).
3. **Other services**: publish job messages, consume `jobs:done`.
4. **Ops**: run the compose stack, scale worker replicas.

When actors conflict, the job message schema is the contract and wins over convenience.

## Scope

**In MVP (V1–V5)**
- Base image matrix: GDAL 3.11, 3.12 (default) and 3.13 built from source, on Python 3.12 and 3.13, with numpy 2.x; versions in `base/matrix.json`, smoke-tested in CI.
- Trigger: Redis-protocol (Valkey) Streams message → job.
- Slicer: windowed GeoTIFF read from S3, configurable tile size/overlap, 8-bit RGB PNG tiles.
- Detector: RF-DETR (default) and YOLOX (via ONNX Runtime), CPU.
- Postprocess: merge, cross-tile NMS, georeference, GeoJSON to S3.
- Failure handling: redelivery, idempotency, partial results flagged `incomplete`.
- Visualisation and analytics (V3b): matplotlib PNGs for overlays, tile grid, distributions and stage latency.
- Benchmark harness on COCO (V5); SpaceNet and a local fine-tune follow in V5b (ADR-0010).
- Licence gate: no AGPL (or other strong-copyleft) component in the stack (ADR-0009).

**Post-MVP (designed for, not built)**
- V6: HTTP API (submit, status, result, presigned upload) and tenant-aware keys.
- V7: GPU variant.
- V8: web frontend (after V6).
- Later: Postgres job store, autoscaling, Kubernetes.

**Out**
- Productised training; one fine-tune recipe exists only to produce benchmark weights (V5b).
- Oriented boxes and segmentation. Axis-aligned boxes only.
- Non-GeoTIFF inputs.
- Any AGPL software, including Garage, MinIO and Ultralytics.

## Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| R0 | A job message naming a GeoTIFF yields a GeoJSON of detections with correct map coordinates | Core goal |
| R1 | Container images exist for GDAL + Python 3.12 and 3.13 + numpy 2.x, verified by a smoke test | Core goal |
| R2 | Rasters larger than RAM are processed with bounded memory | Must-have |
| R3 | Objects straddling tile edges are detected once | Must-have |
| R4 | Detector is swappable (RF-DETR, YOLOX) by config | Must-have |
| R5 | Restarted or crashed stages neither lose nor duplicate jobs | Must-have |
| R6 | Bad input or missing tiles produce an explicit error or `incomplete` result | Must-have |
| R7 | Runs locally from one command, no cloud account | Must-have |
| R8 | No AGPL component in the stack; Python packages checked in CI (see ADR-0009 for the OS-layer carve-out) | Must-have |
| R9 | Architecture can add an HTTP API and frontend without changing worker contracts | Must-have (design only in MVP) |
| R10 | Measured detection accuracy and georeferencing error on a public dataset | Nice-to-have |
| R11 | GPU inference | Nice-to-have |

## Architecture

```
            (post-MVP)                       MVP, all in docker compose
 browser ─► frontend ─► api ───┐
                               ▼
 CLI / other services ──► Valkey Streams ──► slicer ──► tiles ──► detector ──► detections ──► postprocess
                               ▲                │                    │                              │
                               │          object store (S3 API: SeaweedFS) ◄──────────────────────┘
                               └── job status hash ◄── every stage updates
```

- **Stateless workers.** All state is in Valkey (messages, per-job aggregation, status) or the object store. Any worker can be killed or scaled.
- **Single submission path.** The CLI, and later the API, both call one `submit_job()` in `libs/geopipe`, which validates the message and writes it to `jobs:in`.
- **Tenant-aware from day one.** Messages carry `tenant_id` (default `local`); S3 keys follow `tenants/<tenant_id>/…`. No enforcement in MVP, no later rewrite.
- **Object store behind the S3 API only.** No vendor features, so SeaweedFS can be swapped for AWS S3 or another store by changing endpoint and credentials.
- **Observable.** Each stage updates `job:<id>:status` (queued, slicing, detecting, finalising, done, error, incomplete) with progress counts, which is what a status API or frontend will read. Stages expose a health check.
- **Compose services (MVP):** `valkey`, `seaweedfs` (+ bucket init), `slicer`, `detector`, `postprocess`. Post-MVP adds `api`, `frontend`, `postgres`.

## Shape

| Part | Mechanism | ADR |
|------|-----------|-----|
| S0 | Base image build matrix (3.12, 3.13) with `verify-gdal` smoke test and CI | 0007 |
| S1 | Streams per hop (`jobs:in` → `tiles` → `detections` → `jobs:done`), consumer group per stage, ack after success | 0001 |
| S2 | Slicer: GDAL `/vsis3/` windowed reads → 8-bit RGB PNG tiles in S3 scratch → one `tiles` message per tile with `total` | 0002 |
| S3 | Detector (GDAL-free image) runs the configured backend on each tile | 0003, 0004 |
| S4 | Postprocess: per-job Redis hash, finalise on count or timeout, NMS, georeference, GeoJSON | 0005, 0006 |
| S5 | Shared `libs/geopipe`: versioned message models, `submit_job`, status helpers, S3 client, tiling math | 0008 |
| S6 | Compose stack, object store and Valkey | 0001, 0009 |
| S7 | Benchmark harness (COCO now, SpaceNet converters later) | 0010 |

## Affordances

| Affordance | Kind | Wires to |
|------------|------|----------|
| `geopipe submit s3://bucket/key.tif` | CLI | `submit_job()` → `jobs:in` |
| `XADD jobs:in …` | Entry point for other services | Slicer group |
| `jobs:done` | Output stream | Downstream consumers (later: API webhooks) |
| `job:<id>:status` | Valkey hash | CLI `geopipe status`, later API/frontend |
| `s3://<bucket>/tenants/<tid>/results/<job_id>.geojson` | Output file | QGIS, scripts |
| `s3://scratch/tenants/<tid>/<job_id>/<idx>.png` | Intermediate | Slicer writes, detector reads, deleted by postprocess after finalise, plus a sweep for orphans |
| `jobs:dead` | Stream | Messages that exhausted retries |
| `geopipe bench` | CLI | Runs jobs over a dataset split and scores output |

## Implementation decisions

- **Job message** (`jobs:in`): `schema_version`, `tenant_id`, `job_id` (optional; default hash of tenant+bucket+key+ETag+options), `bucket`, `key`, `options` {`model`, `tile_size` 640, `overlap` 0.2, `conf` 0.25, `iou` 0.5, `output_prefix`}.
- **Tile message**: `job_id`, `tenant_id`, `idx`, `total`, `window` {x_off, y_off, w, h} in raster pixels, `tile_ref`, options needed downstream. Geotransform, CRS WKT, raster size and stretch stats go once to `job:<id>:meta`.
- **Detection message**: `job_id`, `idx`, `total`, `boxes` [{x1,y1,x2,y2,score,class_id,class_name}] in tile pixels.
- **Tiling**: stride = tile_size × (1 − overlap); edge windows shifted inward; fully-nodata tiles skipped and not counted in `total`.
- **Band handling**: bands 1–3 as RGB; 1 band replicated; ≥4 bands use the first three; uint16/float stretched to 8-bit using per-image 2–98 percentiles from an overview, shared by every tile.
- **Postprocess**: add window offset, drop boxes cut by an interior tile edge (when overlap > 0) → class-wise NMS → pixel corners to CRS via geotransform → reproject to EPSG:4326.
- **GeoJSON**: Polygon per box with `class_name`, `class_id`, `score`, `pixel_bbox`, `job_id`; collection-level `schema_version`, `source_crs`, `model`, `incomplete`, `missing_tiles`.
- **Config and secrets**: environment variables only; credentials never logged; `.env` gitignored.
- **Tooling**: Python, `uv`, `pytest`, `ruff`; one workspace with `libs/geopipe` and a package per stage.
- **Licence gate**: CI checks each image's installed Python packages and fails on AGPL or GPL; Debian userland and shared libraries are reviewed separately (ADR-0009).

## Testing approach

Test through the Valkey/S3 boundary, which is what other services and a future API see. Fixtures are synthetic GeoTIFFs with rectangles at known map coordinates; a stub detector returns those boxes, which isolates the geo math from model quality. Base images are tested by the `verify-gdal` smoke test for each Python version. Real-model and SpaceNet tests are slow and run separately. Per-slice plans are in `SLICES.md`.

## Lightweight CPU detector

A feature series (L1-L5 in `SLICES.md`, decisions in ADR-0014): remove pipeline overhead first, add YOLOX-nano/tiny and RF-DETR Nano as opt-in backends, judge them on SpaceNet against a stated bar, add INT8 variants, and end with a torch-free detector image. RF-DETR Small stays the default. Out of scope: ARM images, new default model, other detector families.

## Studio: explore, bring your own, fine-tune, benchmark

**Problem.** The models we trained are only visible as tables in the docs, and trying a different dataset or model means editing scripts and running `docker run` commands by hand.

**Solution.** A studio in the existing indah frontend (ADR-0015) with two halves. *Explore*: browse a dataset's images with ground-truth boxes, pick a model trained on it, see its metrics, PR curve and its predicted boxes against the ground truth on each image. *Train*: upload your own dataset (COCO) and/or ONNX model, fine-tune a preset on a GPU worker (ADR-0016), run a benchmark on any split, and compare models. The built-in SpaceNet Khartoum dataset and the fine-tuned models from ADR-0014 are the first things you can explore.

**Users.** First, the owner and small teams running it locally; the contracts are tenant-scoped so a hosted service is possible later. Agents use the same API; the browser is one client.

**Scope boundary.** In: COCO datasets, single class, axis-aligned boxes; ONNX models with a YOLOX or RF-DETR layout; four fine-tune presets; in-process benchmarks; explore views. Out: labelling or annotation editing, segmentation, multi-class training UI, raw hyperparameters, `.pth` uploads (ADR-0017), remote GPUs, public hosting, rate limits and billing, ARM.

**Requirements**

| ID | Requirement | Slice |
|----|-------------|-------|
| RS1 | Browse a registered dataset by split with ground-truth boxes, attribution visible | S1 |
| RS2 | See a model's metrics, PR curve and predicted against ground-truth boxes per image | S2 |
| RS3 | Upload a COCO dataset up to 5 GB straight to the store, with a validation report | S0, S3 |
| RS4 | Fine-tune a preset on a chosen dataset from the browser with live progress; the result is a registered ONNX model | S4 |
| RS5 | Run a registered model on a raster from the Detect tab | S5 |
| RS6 | Upload an ONNX model safely and benchmark any model on any dataset split, with the same metrics as the CLI | S6 |
| RS7 | Delete things, enforce limits, and have a browser smoke test in CI | S7 |
| RS8 | Tenants never see each other's datasets, models or runs; nothing uploaded is unpickled | all |

**Shape.** Three new pieces: API routers for datasets, models and runs; a `trainer` stage consuming `ingest`, `train` and `bench` (ADR-0016); and an on-demand `user:<id>` loader in the detector (ADR-0017). State is JSON manifests in the object store under `tenants/<tid>/...` and `shared/...`, listed through Valkey sorted sets. The frontend is a client of the API only (ADR-0015).

**Testing approach.** As elsewhere, through the Valkey and S3 boundary and the indah session dispatch path. The trainer takes its training function as a seam, so CI exercises the whole flow with a fake trainer that registers a stored ONNX file; real training runs are slow, need the GPU, and are run by hand with their numbers recorded in `docs/benchmarks/`.

**Assumed defaults (studio)**

| ID | Assumed | Cost if wrong |
|----|---------|---------------|
| S-A1 | A dataset without split folders is split 80/10/10 by sorted file name; Roboflow-style `train/valid/test` folders are used as given | Users with their own split see a different one; a manifest field would override |
| S-A2 | Presets, not hyperparameters | Power users ask for raw settings; a later "advanced" panel |
| S-A3 | Per-image predictions are saved with every benchmark run | Storage grows; they can be dropped for old runs |
| S-A4 | Limits: dataset 5 GB and 20,000 images, model 500 MB, one active training per tenant | Tune in config |
| S-A5 | Local-only, trusted network; no rate limits | Needs the hosting milestone before any public exposure |

## Assumed defaults

| ID | Assumed | Cost if wrong |
|----|---------|---------------|
| Q8 | Rasters are GeoTIFF (ideally COG), 0.5–5 GB, 1–8 bands, uint8 or uint16 | Tile memory model changes; non-COG makes remote windowed reads slow (V3 measures) |
| Q9 | Tile 640 px, overlap 20% | Small objects near seams may split; tunable per job |
| Q10 | Output CRS EPSG:4326 | A metric-CRS option is a small addition |
| Q11 | CPU only in MVP | V7 adds GPU |
| Q13 | Axis-aligned boxes suffice | Oriented datasets need conversion |
| Q22 | Debian trixie as base OS, GDAL compiled from source | Source builds take minutes per GDAL version and cover a chosen driver set, not all of GDAL's |
| Q23 | Valkey Streams as queue, Valkey hash for MVP job status | Hosted service moves status to Postgres |
| Q24 | SeaweedFS handles `/vsis3/` range reads and presigned URLs | Fallback to RustFS or another Apache-2.0 store (V1 tests it) |

## Open risks

- **No aerial weights.** Off-the-shelf COCO weights fail on overhead imagery. V2 proves plumbing only. V5 benchmarks on COCO, where the shipped weights work; V5b adds a one-off fine-tune on SpaceNet buildings, run locally on the RTX 3080 Ti (no rental). Earliest reveal: V2.
- **ML wheels vs Python versions.** RF-DETR/torch and YOLOX are unverified on 3.13. Detector image defaults to 3.12; YOLOX runs through ONNX Runtime to avoid the `yolox` and torch dependency at inference. Earliest reveal: V2.
- **SeaweedFS compatibility** with GDAL's S3 client (range reads, path-style addressing). Earliest reveal: V1.
- **Dataset specifics.** SpaceNet GeoTIFF band layout, bit depth and footprint format must be inspected with `gdalinfo` before V5b; file sizes suggest 16-bit RGB tiles but that is inferred, not read.
- **Licence claims** for Valkey, SeaweedFS and YOLOX come from search results or memory; see ADR-0009.
- **SpaceNet access** was checked (ADR-0010): public bucket, anonymous access works, CC BY-SA 4.0, per-tile files allow a small subset.
