# geo-pipeline: Slices

Vertical increments, each ending in something demonstrable. MVP is V1–V5. V1 confronts the riskiest unknowns without any ML model: the base image matrix, SeaweedFS compatibility with GDAL, and the pixel-to-map coordinate round trip.

## V1: Base images and geo round trip with a stub detector

**Delivers:** R0, R1, R7, R8 (partial), part of R2

**Build plan**

1. `base/Dockerfile` (exists; built and verified locally for 3.12 and 3.13) plus a CI build matrix running `verify-gdal`.
2. `compose.yaml`: Valkey, SeaweedFS, bucket-init job.
3. `libs/geopipe`: message models (with `tenant_id`), `submit_job`, status helpers, S3 client, tiling math.
4. Slicer on `geo-gdal`: windowed `/vsis3/` read, PNG tiles to scratch, `tiles` messages, status updates.
5. Stub detector backend (box at tile centre).
6. Postprocess: aggregate by count, georeference, GeoJSON, `jobs:done`.
7. Synthetic GeoTIFF generator with rectangles at known coordinates.
8. `geopipe submit` and `geopipe status` CLI.
9. Licence check script run over installed packages in each image.

**Demo:** `docker compose up`, `geopipe submit s3://imagery/synth.tif`, `geopipe status <id>` shows progress to `done`; open the GeoJSON over the raster in QGIS and the boxes sit on the drawn rectangles.

**Rests on assumptions:** Q8, Q10, Q22 (Debian trixie base), Q24 (SeaweedFS and GDAL). If Q24 fails, swap the object store; nothing else changes.

### Test plan

#### End-to-end
- Synthetic 4096×4096 EPSG:32633 GeoTIFF with 5 rectangles: feature centres land within 1 raster pixel of the known coordinates.
- `jobs:done` appears with the output URI within 60 s and `job:<id>:status` ends at `done`.
- Both `geo-gdal:py3.12` and `py3.13` pass `verify-gdal` (numpy ≥ 2, `/vsis3/`, GTiff/COG/PNG drivers).

#### Integration
- Slicer on a fixture publishes `total` messages and `total` PNGs exist in scratch.
- Tile windows cover 100% of the raster with no gaps.
- Postprocess does not write output until all tiles are present.
- GDAL reads a window from SeaweedFS over `/vsis3/` (range read) and a presigned GET works.

#### Unit
- Window generation: stride, edge shift-inward, full coverage for odd sizes.
- Pixel→CRS transform for north-up and rotated geotransforms.
- Message models reject an unknown `schema_version` and require `tenant_id`.
- Licence check fails on a fake AGPL package.

## V2: Real detector (RF-DETR, CPU)

**Delivers:** R0, R4 (first backend)

**Build plan**

1. Detector image (GDAL-free, Python 3.12 default).
2. `Detector.detect(images) -> list[list[Box]]`; backends `stub` and `rfdetr`.
3. Config: model size (Small default), weights path, conf, device.
4. Consume `tiles`, publish `detections`, ack after publish.

**Demo:** same command with `model=rfdetr`; GeoJSON holds real detections on a fixture image whose COCO classes are visible (street scene with cars).

**Rests on assumptions:** Q11, RF-DETR installs on the chosen Python (PLAN §Open risks).

### Test plan

#### End-to-end
- A real photo (COCO val2017 #39769, two cats) wrapped as a GeoTIFF yields ≥2 `cat` features with score ≥ 0.5, all inside the raster bounds.
- Switching `model=stub` ↔ `rfdetr` needs no change in other stages.

#### Integration
- Detector reads a tile PNG from S3 and emits a detection message with matching `job_id`/`idx`/`total`.
- An unknown `DETECTOR_MODELS` entry fails startup with a clear error, not at the first message. (Weights are baked into the image, so "missing weights" cannot occur; custom weights arrive in V5.)

#### Unit
- Backend output normalises to the shared `Box` model.
- Confidence filter applied.

## V3: Large rasters, overlap, seam dedupe

**Delivers:** R2, R3

**Build plan**

1. Overlapping tiles by default; postprocess drops boxes cut by an interior tile edge (another tile sees the object whole), then applies class-wise NMS. Known limit: objects larger than the overlap zone can be split.
2. Bounded-memory slicer: one window at a time; stretch stats from an overview.
3. 8-bit stretch for uint16 and 1-band inputs; skip all-nodata tiles.
4. 2–5 GB synthetic COG and non-COG variants; record read time and peak RSS for each.

**Demo:** run a multi-GB image; show slicer peak memory and a seam-straddling object appearing once.

**Rests on assumptions:** Q8, Q9.

### Test plan

#### End-to-end
- A rectangle exactly on a tile seam yields exactly 1 feature.
- A 2 GB raster completes with slicer peak RSS under 1.5 GB.

#### Integration
- uint16 and 1-band rasters produce valid 8-bit tiles with a consistent stretch across tiles.
- All-nodata tiles are skipped and `total` reflects that.

#### Unit
- NMS keeps the higher score on duplicates and keeps different classes.
- Percentile stretch is deterministic for a given overview.

## V3b: Visualisation and pipeline analytics

**Delivers:** R10 (partly), supports R2/R3 review

**Build plan**

1. Stages record timings and counts in the job status hash: slice, summed detect and finalise seconds, raw vs post-NMS box counts, submit and done times.
2. `geopipe viz <job_id>...` writes PNGs with matplotlib (vanilla, no server):
   - an overlay of the final detections on the input (uint16 rasters use the stored stretch),
   - a tile-grid view shaded by detections per tile, with nodata tiles that were skipped marked,
   - histograms of score and box size, and counts per class,
   - stage time and throughput bars comparing all the jobs given.
3. A short doc on opening raster + GeoJSON in QGIS (`docs/qgis.md`).

An interactive dashboard is out of scope here. indah (the owner's reactive UI framework) is the candidate for it and for the V8 frontend.

**Demo:** run the cats and synthetic jobs, run `geopipe viz` on both, open the PNGs: boxes on the image, tile grid, distributions, per-stage latency.

### Test plan

#### End-to-end
- `geopipe viz` on two finished jobs (one with detections, one with none) writes every PNG.

#### Integration
- Status hash contains detect and finalise seconds, raw and final box counts, and submit/done times after a job.

#### Unit
- Distribution, per-tile count and throughput numbers are computed correctly, including empty input and a missing submit time.
- Every plot renders with detections and with none.

## V4: Failure behaviour

**Delivers:** R5, R6

**Build plan**

1. `XAUTOCLAIM` for stuck messages; retry counter; `jobs:dead` after N attempts.
2. Idempotent postprocess (finalise lock, deterministic output key).
3. Per-job timeout: finalise partial output as `incomplete` with `missing_tiles`.
4. Unreadable or non-georeferenced TIFF → `status: "error"` on `jobs:done` and in the status hash.

**Demo:** kill the detector mid-job and restart it; the job completes correctly. Submit a corrupt file and see an error event. Drop one tile message and see an `incomplete` GeoJSON after the timeout.

### Test plan

#### End-to-end
- Kill and restart the detector during a 100-tile job: GeoJSON identical to an uninterrupted run, no duplicates. (Verified by hand; automated at the bus level because the test runner cannot control containers.)
- Corrupt TIFF gives `status: "error"` in under 30 s and no GeoJSON.
- Submitting the same job twice yields one output object and no duplicate features.

#### Integration
- A tile that keeps failing reaches `jobs:dead` after the retry limit and fails its job; an unparseable message is dead-lettered at once.
- Timeout path writes `incomplete: true` with the exact missing indices.

- A crashed worker's message is reclaimed, and a slow handler is not duplicated by a second consumer.
- Four concurrent finalisers produce exactly one output and one done event.

#### Unit
- Backoff doubles per delivery and is capped.

## V5: YOLOX backend and COCO benchmark

**Delivers:** R4, R10

**Build plan**

1. YOLOX backend through ONNX Runtime (Apache-2.0 model, `yolox_s.onnx` baked into the detector image; no Ultralytics).
2. `geopipe bench`: wrap COCO images as GeoTIFFs, run each through the real pipeline per model, convert the GeoJSON back to COCO detections, and score with `pycocotools` plus precision/recall/F1 at IoU 0.5 and latency. Writes a markdown report and PNG plots (precision-recall curves, IoU histogram, latency).
3. `make coco-data` fetches COCO's annotations once (241 MB zip, only `instances_val2017.json` is kept); images are fetched per file as needed.
4. A 5-image annotation excerpt is committed as a test fixture.

**Demo:** one report comparing RF-DETR Small and YOLOX-s on the same 200 COCO val images: mAP, F1, latency per image.

**Rests on assumptions:** COCO scores for everyday objects only; they do not predict overhead performance (see V5b).

### Test plan

#### End-to-end
- `geopipe bench` on the 5-image fixture produces a report and plots for both models, with RF-DETR mAP@.5 above a sanity floor.
- Same inputs and config give identical metrics (deterministic).

#### Integration
- A YOLOX job through the stack returns plausible detections on the cats photo (two cats found).

#### Unit
- YOLOX letterbox and decode: boxes map back to original pixel coordinates; NMS and the confidence threshold are applied.
- Matching and P/R/F1 agree with a hand-computed case; perfect detections score mAP 1.0.

## V5b: SpaceNet benchmark and fine-tune (done)

**Delivers:** R10 on overhead imagery

**Build plan**

1. Fetch a small subset (about 300 tiles, ~750 MB) of SpaceNet 2 `PS-RGB` and `geojson_buildings` with anonymous S3 access; inspect one tile with `gdalinfo` (bands, bit depth, CRS): done, `make spacenet-data`. CC BY-SA 4.0, never committed (ADR-0010).
2. Converter: footprint polygons to axis-aligned boxes in map coordinates, round-tripped through the geotransform.
3. CUDA training image (shared with V7); one documented full fine-tune of RF-DETR Small and YOLOX-s on this machine's RTX 3080 Ti (single class `building`). No QLoRA: the models are small.
4. Reuse the V5 harness with a SpaceNet loader and 16-bit stretch; report the same metrics.

**Demo:** the V5 report, rerun on held-out SpaceNet tiles with fine-tuned weights.

### Test plan

#### End-to-end
- `geopipe bench` on 20 held-out tiles reports metrics for both models.

#### Integration
- Ground truth scored against itself gives 1.0; converted boxes round-trip pixel to map to pixel within 1 px.

#### Unit
- A polygon's converted box encloses it.

## V6: HTTP API (done)

**Delivers:** R9

**Build plan:** FastAPI service in compose with `POST /jobs` (calls `submit_job`), `GET /jobs/{id}` (reads status), `GET /jobs/{id}/result` (presigned GeoJSON URL), `POST /uploads` (presigned PUT). API-key auth placeholder; tenant derived from the key.

**Demo:** upload a TIFF and fetch its GeoJSON using only curl against the API.

### Test plan

#### End-to-end
- curl flow: upload → submit → poll status → download GeoJSON, matching the CLI result.

#### Integration
- Two tenants cannot read each other's results (keys and presigned URLs scoped by `tenants/<tid>/`).

#### Unit
- Request validation uses the same message models as the workers.

## V7: GPU (done)

**Delivers:** R11

**Build plan:** CUDA detector image variant, `device=cuda`, batch size config, throughput measurement.

**Demo:** the V3 large raster on CPU vs GPU with tiles/s for each.

### Test plan

#### End-to-end
- Same job on CPU and GPU gives features matching within a score tolerance at IoU ≥ 0.9.

#### Integration
- `device=cuda` starts on a GPU host and fails with an explicit error when none exists.

#### Unit
- Batch assembly respects the configured size.

## V8: Web frontend (done, on indah)

**Delivers:** R9 (user-facing half)

**Build plan:** choose the stack (ADR); upload form using presigned PUT; live job progress from the status endpoint; map view of detections over the raster with class and score filters; job history and GeoJSON download; sign-in mapped to a tenant.

**Demo:** in a browser, sign in, upload a GeoTIFF, watch progress, see boxes on a map, download the GeoJSON.

### Test plan

#### End-to-end
- Browser test: upload, wait for done, detections visible on the map, filter by score changes the visible count.

#### Integration
- Frontend talks only to the V6 API; no direct Valkey or S3 credentials in the browser.

#### Unit
- Score and class filters reduce the feature set as expected.

## L1: Pipeline overhead (done; lightweight detector series, ADR-0014)

**Delivers:** the precondition for any model speedup to be visible end to end

**Build plan:** slicer writes PNG tiles with `compress_level=0` (amend ADR-0002); `DETECTOR_THREADS` env (default 4) applied to ONNX Runtime and torch; detector replicas via `docker compose up --scale detector=N` documented and tested (consumer group splits tiles); `geopipe bench` reports slicer ms/tile.

**Demo:** the stub-model pipeline on the real-imagery mosaic goes from 13.5 tiles/s to at least 30 tiles/s, with the same GeoJSON. Measured: 33.5 tiles/s with one detector (slicer 18 ms/tile), 48 with two. Level 0 alone gave 22.6; overlapping encode and upload on 4 threads gave the rest. Replica splitting was checked by hand (two consumers, no pending messages); the stack tests cannot scale compose services.

### Test plan

#### End-to-end
- Same job before and after: identical features; stub-pipeline tiles/s at least doubles (measured, three runs).
- Two detector replicas process one job with no tile lost or duplicated in the output.

#### Integration
- Slicer tiles decode to the same pixels as before.

#### Unit
- `DETECTOR_THREADS` reaches the session options; an invalid value fails at startup.

## L2: Nano, tiny and RF-DETR Nano backends (done)

**Delivers:** the candidate models, selectable per job

**Build plan:** YOLOX decoder and letterbox take size and grid from the model; export and register `yolox-nano`, `yolox-tiny`; register `rfdetr-nano`; COCO-200 benchmark rows for each at 1 and 4 threads; document the "fast" preset in README.

**Demo:** `make bench-coco` prints speed and mAP for the new models next to YOLOX-s and RF-DETR Small.

**Rests on assumptions:** L5 (input size, decided in L3).

### Test plan

#### End-to-end
- A job with `model=yolox-nano` completes on the COCO mosaic with plausible detections.

#### Integration
- Each backend loads from `DETECTOR_MODELS` and an unknown name still fails with a clear error.

#### Unit
- Decoder grid is correct at 416 and 640 (existing decode tests parametrised by size).

## L3: SpaceNet fine-tunes and the verdict (done)

**Delivers:** the data that decides which models ship

**Build plan:** fine-tune nano, tiny and RF-DETR Nano on the existing SpaceNet split; benchmark at 416 and 640 inputs; apply the ADR-0014 bar; remove backends that miss it; record the table in `docs/benchmarks/` and update ADR-0014 with the result.

**Result:** YOLOX-tiny at 416 px passes (0.564 mAP@.5, 3.1x faster than YOLOX-s); tiny at 640, and nano at both sizes, are removed; RF-DETR Nano passes on accuracy but not on speed against YOLOX-s and is kept pending a decision. See `docs/benchmarks/spacenet-khartoum.md`.

**Demo:** a report table of mAP@.5, F1, ms per tile (1 and 4 threads) and end-to-end tiles/s for every candidate, each marked pass or fail against the bar.

### Test plan

#### End-to-end
- `make bench-spacenet` reproduces the table from the saved weights.

#### Unit
- Existing SpaceNet prep tests (no change expected).

## L4: INT8 quantisation (done)

**Delivers:** the faster variant of whichever YOLOX models passed L3

**Build plan:** `scripts/quantise_onnx.py` (ORT static QDQ, per-channel, calibration tiles taken from the SpaceNet training split); `*-int8` backends; accuracy and speed rows in the L3 table.

**Result:** YOLOX-s INT8 0.573 mAP@.5 (-1.3 against FP32), 2.4x to 2.9x faster; YOLOX-tiny INT8 0.559 (-0.5), 1.5x to 2.4x faster. Both registered. Numbers in `docs/benchmarks/lightweight-detector.md`.

**Demo:** the report shows each INT8 model's mAP delta against its FP32 model and the speedup; the bar is within 2 points.

### Test plan

#### End-to-end
- INT8 model run on the benchmark stays within 2 mAP points of FP32; otherwise the INT8 backend is not registered.

#### Unit
- The script itself fails if the quantised model's output shape differs from the FP32 model's (checked on every run; no host test, since onnxruntime is not in the unit layer).

## L5: Torch-free ONNX-only detector image (done)

**Delivers:** a small CPU image

**Build plan:** export RF-DETR to ONNX and add an ORT backend; second Dockerfile (or build arg) without torch; compose profile `slim`; CI builds both; licence check on the new image.

**Result:** 1.05 GB (4.03 GB for the default), 0.43 s model load against about 6 s, identical mAP and output. Numbers in `docs/benchmarks/lightweight-detector.md`.

**Demo:** `docker images` shows the slim detector at about 0.5 GB (it is 1.05 GB); a job on it finishes and matches the torch image's detections within tolerance.

### Test plan

#### End-to-end
- Same job on both images: features match at IoU 0.9 within a score tolerance.

#### Integration
- The slim image has no `torch` and passes `make licences`.

#### Unit
- The ONNX RF-DETR post-processing matches the torch output on a stored fixture tensor.

## L6: Publish the weights on Hugging Face (tooling done; the upload waits for a Hub repo and token)

**Delivers:** the fine-tuned models that passed the L3/L4 bar, downloadable by others

**Build plan:** `scripts/publish_model.py` uploads one model (ONNX, or the RF-DETR checkpoint) to a Hugging Face model repo with a generated model card; card template filled from the benchmark report (metrics, speed at 1 and 4 threads, training data and split, one-city caveat, the commit and command that produced it); licence CC BY-SA 4.0 with SpaceNet attribution and a link to the source; the GitHub README links to the model pages. Only models that passed the bar are published. Nothing derived from COCO is uploaded (ADR-0010).

**Result:** `scripts/publish_model.py` (`make publish-model REPO=user/name`) stages `yolox-tiny-buildings` (FP32 and INT8) with a generated card (`scripts/model_card.md`), checks the bar from the committed results and refuses anything that fails it (RF-DETR Nano is refused as the bar is written), and uploads only with `UPLOAD=1` and `HF_TOKEN`. The dry run was checked; nothing has been uploaded yet.

**Demo:** `hf_hub_download` of the published ONNX file into a clean directory, then a job on `data/models` weights from that file reproduces the benchmark mAP@.5 within 0.005.

**Rests on assumptions:** CC BY-SA 4.0 for the fine-tuned weights is the cautious reading of SpaceNet's licence (whether weights are a derivative of training data is unsettled); an Apache-2.0 release is not planned.

### Test plan

#### End-to-end
- Download from the Hub and run `make bench-spacenet` on it; mAP@.5 matches the model card.

#### Unit
- The model card renders with the licence, attribution and every metric from `results.json`; the script refuses a model that is not marked as passing.

# Studio series (ADR-0015, ADR-0016, ADR-0017)

Requirements RS1 to RS8 are in `PLAN.md`. Order: the riskiest unknown first (S0), then read-only Explore, then upload, training, user models. Real training runs need the GPU and are not in CI; CI uses a fake trainer behind a seam.

## S0: Spike: direct browser upload to the store (done)

**Delivers:** the decision for RS3: CORS plus presigned PUT, or streaming through the frontend

**Build plan:** configure CORS on the SeaweedFS S3 endpoint (or find out it cannot); a throwaway page that PUTs a 300 MB file to a presigned URL from a real browser; measure the browser and frontend memory; record the result and the chosen path in ADR-0015.

**Result:** works up to 4.9 GB, memory growth 0; see ADR-0015. A bucket CORS rule is required (SeaweedFS reflects any origin without one).

**Demo:** a 300 MB file is in the store, uploaded from a browser, with the frontend process's memory growth under 100 MB; or a written reason and the working fallback.

**Rests on assumptions:** SeaweedFS supports bucket CORS for browser PUTs (unverified).

### Test plan

#### End-to-end
- A browser (Playwright) uploads 300 MB to a presigned URL; the object's size and checksum match.

#### Integration
- The same upload from a different origin fails without the CORS rule and succeeds with it.

#### Unit
- None (a spike); the result is a paragraph in ADR-0015.

## S1: Explore a dataset

**Delivers:** RS1, and registry v0 (datasets)

**Build plan:** dataset manifests (ADR-0016) and `geopipe register-builtin`, which copies the SpaceNet COCO files and images to `shared/datasets/spacenet-khartoum/` with thumbnails and licence and attribution text; API `GET /datasets`, `/datasets/{id}`, `/datasets/{id}/images?split&page`, image and annotation endpoints, tenant and `shared/` visibility rules; statistics in `geopipe.viewer` (boxes per image, box size distribution); the Explore tab with dataset select, split filter, `Gallery`, `ImageOverlay` with ground-truth boxes, two charts, attribution banner. Split `stages/frontend/app.py` per tab.

**Demo:** open Explore, pick SpaceNet Khartoum, filter to the test split (30 images), click one and see its ground-truth boxes with the CC BY-SA attribution on screen.

**Rests on assumptions:** S-A1 (split rule), S-A5 (local-only).

### Test plan

#### End-to-end
- Through the indah session path: with a small fixture dataset registered, the gallery lists the right number of images per split and a selected image shows exactly its annotation count of boxes.

#### Integration
- Tenant B cannot list or fetch tenant A's dataset; both can read `shared/`; no tenant key can write or address `shared/`.

#### Unit
- Statistics functions on a fixture; manifest schema validation rejects a missing licence field.

## S2: Explore a model and its results

**Delivers:** RS2

**Build plan:** `geopipe bench` writes `results.json` and `predictions.json` per run (S-A3; the in-process scorer arrives in S6); `register-builtin` also registers the fine-tuned models of ADR-0014 with their metrics; API for models and runs; the Explore model view: model select, `Stat` cards (mAP, F1, speed), PR curve, and a results view with ground truth against predictions on each image, a score slider, per-image true and false positives and misses, and a worst-images list. Say whether a number came from the pipeline or the in-process scorer (ADR-0017).

**Demo:** pick yolox-tiny-buildings, see mAP@.5 0.564 and open its worst test tile with predicted and true boxes overlaid.

### Test plan

#### End-to-end
- The metrics shown equal `docs/benchmarks/lightweight-detector/` values to 3 decimals; the boxes drawn at a score threshold match `filter_features`.

#### Integration
- Rescoring a saved `predictions.json` reproduces the saved metrics.

#### Unit
- Per-image TP/FP/FN counting and PR-curve points on a fixture.

## S3: Upload a dataset

**Delivers:** RS3

**Build plan:** the direct-upload indah component and `POST /uploads/dataset` (presigned PUT, per S0); `POST /datasets` queues an `IngestJob`; the `trainer` stage (small image, introduced here with only the ingest handler) unzips safely, detects Roboflow-style splits or applies S-A1, validates the COCO file (missing images, boxes outside the image, empty images, duplicate names, class list), writes thumbnails, annotations and the manifest, and reports; the Train tab's dataset form (name, licence, attribution) and a validation report view.

**Demo:** upload the 730 MB SpaceNet zip in a browser; the frontend's memory growth stays under 100 MB; the validation report appears; the dataset is in Explore.

**Rests on assumptions:** S-A1, S-A4.

### Test plan

#### End-to-end
- A generated zip through the API becomes a ready dataset; zips with path traversal, a size bomb, a missing image or an invalid COCO file are rejected with a report naming the problem.

#### Integration
- Slow: a 300 MB upload leaves the frontend process's memory growth under 100 MB.

#### Unit
- COCO validator, zip guards, split detection.

## S4: Fine-tune from the browser

**Delivers:** RS4

**Build plan:** `stages/trainer` with its small CPU image (ingest and bench, built in CI) and the GPU variant (training, `compose.gpu.yaml`); `TrainJob`; the four presets; one active training per tenant; progress (epoch, validation AP) parsed into the run status; cancel; ONNX export and model registration at the end; the Train tab: dataset, preset, epochs, start, `Steps` and `Progress`, a live loss chart, run history. The training function is a seam; no GPU means an explicit failure.

**Demo:** in the browser, fine-tune the `fast` preset on the 240-tile SpaceNet split; the run ends with a registered model that shows up in Explore.

### Test plan

#### End-to-end
- CI: with a fake trainer the whole flow (start, progress, finish, model registered) works through the UI session path. By hand on the GPU: `fast` on the SpaceNet split reaches mAP@.5 within 0.03 of 0.564, and the number is recorded in `docs/benchmarks/`.

#### Integration
- On a host with no GPU a train job fails at once with the explicit message; a second job from the same tenant waits; cancel stops the run and frees the slot.

#### Unit
- Preset table; training-log parser.

## S5: Run any registered model on a raster

**Delivers:** RS5

**Build plan:** `user:<id>` loader in the detector with an LRU of 3 and the tenant ownership check (ADR-0017), working on both detector images; the Detect tab's model list reads the registry.

**Demo:** run a job in the Detect tab with the model trained in S4 and see its boxes.

### Test plan

#### End-to-end
- A job with `user:<id>` for a registered model completes; a different tenant naming it fails with a clear permanent error.

#### Integration
- A fourth user model evicts the least recently used one; an unknown id fails clearly and is not retried.

#### Unit
- Name parsing and the ownership rule.

## S6: Bring your own model, and benchmark

**Delivers:** RS6

**Build plan:** model upload through the direct-upload component with a declared decoder and class names; ONNX validation (parses, no external-data tensors, shapes match the decoder, at most 500 MB, session built from bytes); `BenchJob` handled in process by the trainer stage using the same decoders, NMS and `score_model`; the Train tab's Benchmark section (models by dataset and split, comparison table); results open in the S2 view.

**Demo:** upload the repo's fine-tuned YOLOX-s ONNX, benchmark it on the SpaceNet test split, and see mAP@.5 of 0.586 (the CLI number).

### Test plan

#### End-to-end
- The built-in YOLOX-s ONNX uploaded as a user model benchmarks within 0.003 of the registered backend's mAP@.5; an ONNX file with external data, and one with the wrong output shape for its declared decoder, are rejected with messages.

#### Integration
- A benchmark run's predictions open in the Explore results view.

#### Unit
- ONNX validator; the in-process scorer equals `score_model` on fixture predictions.

## S7: Delete, limits, and a browser test in CI

**Delivers:** RS7

**Build plan:** delete datasets, models and runs with reference checks (a dataset in use by a running job cannot be deleted); quotas and per-upload limits enforced when the presigned URL is issued; a Playwright smoke test of the studio in CI (sign in, open Explore, see boxes), which also closes the "browser test in CI" loose end.

**Demo:** deleting a dataset removes its objects and listing entries; an upload over the limit is refused before any bytes are sent.

### Test plan

#### End-to-end
- CI browser test: sign in, open Explore on a fixture dataset, boxes are visible.

#### Integration
- Delete removes the objects and the Valkey entries; over-limit presign requests are refused.

#### Unit
- Reference checks and quota arithmetic.
