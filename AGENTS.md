# geo-pipeline

GeoTIFF in S3 → slicer → detector → postprocess → georeferenced GeoJSON, as three workers on Valkey Streams plus an S3-API object store (SeaweedFS), run with Docker Compose. It is meant to become a hosted service later, so contracts carry `tenant_id` and stay stateless.

Build order and acceptance tests: `docs/SLICES.md` (its end-to-end lines are the acceptance criteria). Plan: `docs/PLAN.md`. Decisions: `docs/adr/`. Status: V1-V8 and V5b merged (V5 = YOLOX plus a COCO benchmark, V6 = HTTP API, V7 = GPU detector, V5b = SpaceNet fine-tunes, V8 = web UI on indah). Next: the lightweight CPU detector series L1-L5 (`docs/SLICES.md`, ADR-0014); other open cards are Postgres job records, identity-provider sign-in. Work is tracked on the Pandan board `GEO` (`pandan board use GEO`); move cards as slices land. Trust the code over the docs where they disagree, and fix the doc.

## Layout
- `libs/geopipe/`: shared contracts and helpers; must install on 3.12 and 3.13. Only `raster.py`, `finalise.py`, `viz.py` and `bench.py` need GDAL, matplotlib or pycocotools (`viz` and `bench` extras); the detector must never import them.
- `geopipe/api.py` is the FastAPI app (`api` extra), run by `stages/api/`; it only calls `submit_job` and reads status, and never touches pixels.
- `stages/frontend/app.py` is the web UI (indah, a client of the API only; `geopipe.viewer` holds its filter logic). The slicer writes a JPEG preview next to each result.
- `stages/{slicer,detector,postprocess}/`: one `Dockerfile` and `main.py` each. Slicer and postprocess use the GDAL image; the detector does not have GDAL.
- `base/`: the `geo-gdal` image (GDAL built from source + Python + numpy 2). Versions live in `base/matrix.json`; `build.sh` and `publish.sh` are used by the Makefile and CI.
- `tests/unit/` runs on the host; `tests/stack/` runs inside the `tools` container against the compose stack.

## Commands
- `make check`: lint and unit tests, no Docker. This is the pre-push hook (`make hooks` installs it).
- `make e2e`: stack plus stack tests on a fresh stack (Docker; first build is slow because of torch). `make bench-large`: slow 2 GB benchmark, not in CI. `make bench-coco`: RF-DETR vs YOLOX on 200 COCO images (downloads data). `make spacenet-data`: 300 SpaceNet Khartoum tiles plus COCO files in `data/spacenet/` (CC BY-SA 4.0, never commit; metrics and plots may be). `make train-rfdetr` and `make train-yolox` (GPU, use `train/Dockerfile`) then `make bench-spacenet`; fine-tuned weights live in `data/models/` and are enabled through `DETECTOR_MODELS`.
- `make up-gpu`: detector on the GPU via `compose.gpu.yaml`; never run it alongside the CPU detector (shared consumer group). `make bench-device`: CPU vs GPU tiles/s and parity (needs a GPU, not in CI).
- `make base` (default GDAL, both Pythons), `make base-all` (every GDAL x Python, slow), `make licences`, `make down`. Bare `make` lists targets.

## Conventions
- One branch per slice or fix, PR to `main`, CI green before merge.
- No AGPL anywhere (ADR-0009); check a dependency's licence before adding it.
- Jobs are created only through `geopipe.jobs.submit_job`. Messages live in `geopipe/messages.py` and carry `tenant_id` and `schema_version`.
- Stages talk only through Valkey and S3, never to each other. Pixels never go through Valkey.
- Handlers raise `bus.PermanentError` when retrying cannot help (bad input); any other exception is retried 3 times, then dead-lettered to `jobs:dead` (ADR-0011). Keep handlers idempotent.
- Test through the Valkey/S3 boundary. Update README, `docs/` and this file in the same PR as the change.

## Traps that already cost time
- Presigned URLs must use SigV4 (`store.client` sets it; SeaweedFS rejects boto3's default v2) and are signed for `S3_PUBLIC_ENDPOINT`, so tests inside compose send a `Host: localhost:8333` header.
- GDAL's Python bindings must match libgdal exactly. Do not base on the official `ghcr.io/osgeo/gdal` Ubuntu images: their 3.11/3.12 bindings are compiled against numpy 1.x and fail to import with numpy 2, and no official image has Python 3.13.
- A GDAL dataset must be released (`ds = None`) before its file is uploaded or read; until then the GeoTIFF is incomplete on disk.
- GDAL caches `/vsis3/` listings per process; the slicer clears the cache per job. Its block cache is capped by `GDAL_CACHEMAX` in `compose.yaml` (the default scales with host RAM).
- S3 `DeleteObjects` takes at most 1000 keys per call.
- Job-status counters must be written before the message that lets the job finish, or they race.
- `docker compose run` does not rebuild images; use `--build` (`make e2e` does).
- Host tests need `uv ... --with-editable ./libs/geopipe` (the Makefile does); a cached non-editable build hides new modules.
- In shell loops, `docker compose exec` needs `-T </dev/null` or it eats the loop's stdin. Never `pkill -f` with a pattern that appears in your own command line.
- Submitting identical options is idempotent, so a reused stack returns old results. Tests and benchmarks need a fresh stack (`make e2e` and `make bench-large` start one) or a fresh `tenant_id` (`geopipe bench` does).
- The WSL2 clock can step backwards. Measure in-process durations with `time.monotonic()`; wall timestamps compared across containers can be off.
- COCO images carry non-commercial Flickr licences: never commit or publish them or anything derived from them (ADR-0010). `data/` files written by containers are root-owned, so the host cannot write there.
- Training lives in `train/Dockerfile`, not the inference images. YOLOX's `setup.py` does not build, so its pinned source is on `PYTHONPATH`, and its own ONNX exporter breaks on current torch: use `scripts/export_yolox_onnx.py`. RF-DETR and YOLOX both compile kernels on first use and need gcc/g++ and ninja in the image.
- Subagent `isolation: worktree` branches from the default branch, not the current one, and is deleted if unchanged. Create the worktree yourself when you need a specific commit.
