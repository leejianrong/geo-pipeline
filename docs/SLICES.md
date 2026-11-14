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
