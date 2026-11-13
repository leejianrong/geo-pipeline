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

**Rests on assumptions:** Q8, Q10, Q22 (Debian trixie), Q24 (SeaweedFS and GDAL). If Q24 fails, swap the object store; nothing else changes.

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
- On a known image with 3 clearly visible objects, output has ≥3 features with score ≥ conf.
- Switching `model=stub` ↔ `rfdetr` needs no change in other stages.

#### Integration
- Detector reads a tile PNG from S3 and emits a detection message with matching `job_id`/`idx`/`total`.
- Missing weights fail startup with a clear error, not at the first message.

#### Unit
- Backend output normalises to the shared `Box` model.
- Confidence filter applied.

## V3: Large rasters, overlap, seam dedupe

**Delivers:** R2, R3

**Build plan**

1. Overlapping tiles by default; class-wise NMS across tiles in postprocess.
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
- Kill and restart the detector during a 50-tile job: GeoJSON identical to an uninterrupted run, no duplicates.
- Corrupt TIFF gives `status: "error"` in under 30 s and no GeoJSON.
- Submitting the same job twice yields one output object and no duplicate features.

#### Integration
- Poison message reaches `jobs:dead` after the retry limit.
- Timeout path writes `incomplete: true` with the exact missing indices.

#### Unit
- Retry counter and backoff.
- Finalise lock is exclusive between two postprocess instances.

## V5: YOLOX backend and SpaceNet benchmark

**Delivers:** R4, R10

**Build plan**

1. Inspect one SpaceNet GeoTIFF with `gdalinfo` (bands, bit depth, CRS) and confirm licence and download path (ADR-0010). Download a small subset to `data/` (gitignored).
2. YOLOX backend via ONNX Runtime (Apache-2.0 model; no Ultralytics).
3. Converter: SpaceNet building footprint polygons → axis-aligned ground-truth boxes in map coordinates.
4. One-off, documented fine-tune of RF-DETR and YOLOX on a SpaceNet train subset (single class `building`) to produce benchmark weights.
5. `geopipe bench`: run jobs over a held-out split, match detections to ground truth by IoU in map space, report precision, recall, F1 and mAP@0.5, plus latency per megapixel, as a markdown report.

**Demo:** one report comparing RF-DETR Small and YOLOX on the same held-out SpaceNet tiles.

**Rests on assumptions:** Q13 (axis-aligned boxes), fine-tuned weights are obtainable on this hardware (CPU fine-tuning may be too slow; may need a rented GPU, see `runpod-jobs`).

### Test plan

#### End-to-end
- `geopipe bench` on 20 held-out images produces a report with metrics for both backends.
- Same inputs and config give identical metrics (deterministic).

#### Integration
- Ground truth scored against itself yields precision, recall and mAP of 1.0.
- Converted ground-truth boxes round-trip pixel → map → pixel within 1 px.

#### Unit
- Polygon → axis-aligned box encloses the polygon.
- Matching and mAP agree with a hand-computed small case.

## V6: HTTP API (post-MVP)

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

## V7: GPU (post-MVP)

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
