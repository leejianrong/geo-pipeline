# Questions

## Open forks

None. The L-series forks were all answered on 2026-11-14 (accepted recommendations); see below.

## Register

| ID | Question | Status | Answer or default | Landed |
|----|----------|--------|-------------------|--------|
| Q1 | Trigger mechanism | DECIDED | Redis-protocol message naming an S3 object | ADR-0001 |
| Q2 | S3 store | DECIDED → revised | Must be non-AGPL, so SeaweedFS, not Garage | ADR-0009 |
| Q3 | Models | DECIDED | RF-DETR (default) and YOLOX; no AGPL | ADR-0004 |
| Q4 | Final output | DECIDED | GeoJSON file | ADR-0006 |
| Q5 | Queue semantics | DECIDED | Streams with consumer groups | ADR-0001 |
| Q6 | Old notes and history | DECIDED | Deleted; fresh git repo | n/a |
| Q7 | Base image | DECIDED | Own GDAL images for Python 3.12 and 3.13 with numpy 2.x | ADR-0007 |
| Q8 | Raster size, bands, dtype | ASSUMED | GeoTIFF 0.5–5 GB, 1–8 bands, uint8/uint16 | PLAN |
| Q9 | Tile size and overlap | ASSUMED | 640 px, 20% | PLAN |
| Q10 | Output CRS | ASSUMED | EPSG:4326 | ADR-0006 |
| Q11 | CPU or GPU | ASSUMED | CPU in MVP, GPU in V7 | SLICES V7 |
| Q12 | Queue server | DECIDED (licence rule) | Valkey | ADR-0001 |
| Q13 | Box type | ASSUMED | Axis-aligned | SLICES V5 |
| Q14 | Tile transport | ASSUMED | PNG in S3 scratch, refs in messages | ADR-0002 |
| Q15 | Image split | ASSUMED | GDAL images vs GDAL-free detector image | ADR-0003 |
| Q16 | Aggregation state | ASSUMED | Valkey, finalise by count or timeout | ADR-0005 |
| Q17 | Success criteria | ASSUMED | ≤1 px georef error; seam objects once; bounded RSS; benchmark metrics | SLICES |
| Q18 | Secrets | ASSUMED | Env only, never logged; `.env` gitignored | PLAN |
| Q19 | Versioning | ASSUMED | `schema_version` on messages and GeoJSON; no migrations yet | PLAN |
| Q20 | Benchmark dataset | DECIDED (generic, `.tif`, scorable) | SpaceNet | ADR-0010 |
| Q21 | Aerial weights | ASSUMED | One-off fine-tune on SpaceNet in V5 | SLICES V5 |
| Q22 | Base OS | ASSUMED | Debian trixie, GDAL 3.11-3.13 built from source (default 3.12), Python 3.12 and 3.13 | ADR-0007 |
| Q23 | Job status store | ASSUMED | Valkey hash in MVP, Postgres when hosted | ADR-0008 |
| Q24 | SeaweedFS and GDAL compatibility | ASSUMED | Works; V1 tests it | ADR-0009 |
| Q25 | Hosted-service shape | DECIDED (direction) | Stateless workers, tenant-aware keys, single submit path, API/frontend later | ADR-0008 |
| Q26 | Python versions | DECIDED | 3.12 and 3.13 both supported for GDAL images; detector defaults to 3.12 | ADR-0003, ADR-0007 |
| Q27 | Licence policy | DECIDED | No AGPL anywhere; CI check | ADR-0009 |

## Coverage

| Category | Covered by |
|----------|-----------|
| Primary user and actors | PLAN §Users |
| Scope boundary | PLAN §Scope, Q6 |
| Data model and identity | Q14, PLAN §Implementation (tenant_id, job_id, idx, window) |
| State and storage | Q14, Q16, Q23 |
| Concurrency and conflict | Q5, Q16, SLICES V4 |
| Interfaces and contracts | Q1, Q25, PLAN §Implementation |
| Failure behaviour | SLICES V4 |
| External dependencies | Q2, Q3, Q7, Q12, Q20, Q27 |
| Runtime and deployment | Q15, Q22, PLAN §Architecture |
| Measurable success | Q17, SLICES V5 |
| Security and secrets | Q18, ADR-0008 (tenant isolation, later) |
| Versioning and migration | Q19 |

## L-series: lightweight CPU detector

Facts measured on this machine (i9-12900H, ORT 1.31 CPU, 4 threads, one 640 px tile, forward pass only): YOLOX-nano 416 px 5.4 ms; YOLOX-tiny 416 px 16.9 ms; YOLOX-s 640 px 64.5 ms; YOLOX-s INT8 (static QDQ, per-channel, 30 calibration tiles) 25.9 ms; RF-DETR Nano 384 px 91 ms in ORT and 86 ms in torch; RF-DETR Small 512 px 181 ms in torch. All cores is not faster than 4. With real imagery and a stub model the whole pipeline runs 13.5 tiles/s: the slicer costs about 72 ms per tile (PNG level 6 encode is 56 ms of that; level 0 is 17 ms), so no model faster than about 75 ms per tile improves end-to-end speed today.

| ID | Question | Status | Answer or default | Landed |
|----|----------|--------|-------------------|--------|
| L1 | Candidates to add | ASSUMED | YOLOX-nano, YOLOX-tiny (Apache-2.0, published ONNX, same decoder), RF-DETR Nano (Apache-2.0, same package), INT8 variants of YOLOX | ADR-0014 |
| L2 | Excluded candidates | ASSUMED | DEIMv2: custom non-commercial licence (read in its LICENSE.md). YOLOv6, 7, 9: GPL. YOLOv10, 11, 12: AGPL. Others (PicoDet, NanoDet, D-FINE, RTMDet): different toolchains, no gain over YOLOX family here; revisit only if the above miss the bar | ADR-0014 |
| L3 | How candidates are judged | ASSUMED | Existing COCO-200 (zero-shot sanity) and SpaceNet-30 (fine-tuned, decides); mAP@.5, F1, per-tile ms at 1 and 4 threads, end-to-end tiles/s | SLICES L1-L3 |
| L4 | Threads | ASSUMED | `DETECTOR_THREADS` default 4 (measured: more is not faster); several detector replicas for throughput | SLICES L1 |
| L5 | Input size | DEFERRED | Nano and tiny are 416 native. Try them at tile size 416 (no downscale) and at 640 (letterbox) and keep the better default, from data | SLICES L2 |
| L6 | Quantisation method | ASSUMED | ORT static QDQ INT8, calibrated on tiles from the target data; dynamic quantisation does not help convolutions | SLICES L3 |
| F-L1 | Success bar | ANSWERED | At least 90% of fine-tuned YOLOX-s SpaceNet mAP@.5 (0.586) at least 2x faster; INT8 within 2 points of its FP32 | ADR-0014 |
| F-L2 | Hardware target | ANSWERED | x86-64 only; 1 and 4 threads as small-box proxy; ARM deferred | ADR-0014 |
| F-L3 | Include pipeline overhead fixes | ANSWERED | Yes, slice L1 first | ADR-0014 |
| F-L4 | Torch-free ONNX-only detector image | ANSWERED | Yes, slice L5 last | ADR-0014 |
| F-L5 | Default model | ANSWERED | Opt-in "fast" preset; RF-DETR Small stays the default | ADR-0014 |
| L7 | Where and how to publish the fine-tuned weights | ANSWERED (user, 2026-11-14) | Hugging Face for weights, GitHub for code; CC BY-SA 4.0 with SpaceNet attribution; only models passing the bar | ADR-0014, SLICES L6 |

## S-series: studio (explore, bring your own, fine-tune, benchmark)

Planning 2026-11-15. Facts checked: indah 0.13.0 ships `Tabs`, `Gallery`, `Chart`, `Plot`, `Heatmap`, `MasterDetail`, `SplitPane`, `Stat`, `Steps`, `Upload`, `Progress`, `ImageOverlay` and `CustomComponent`; the frontend's `Upload` buffers in memory and is capped at 200 MB (ADR-0013) while the SpaceNet subset alone is 730 MB; training is a hand-run `docker run`; `geopipe bench` does not save per-image predictions; the detector loads backends from an environment list at startup.

| ID | Question | Status | Answer or default | Landed |
|----|----------|--------|-------------------|--------|
| F-S1 | Which dataset format can users bring | ANSWERED (accepted recommendation) | COCO JSON plus images as a zip, single class, with a validation report; YOLO txt and GeoTIFF plus footprints later as converters | ADR-0015, S3 |
| F-S2 | Where fine-tuning runs | ANSWERED | A `trainer` stage on the bus, on this GPU, one job at a time; no CPU fallback; remote GPUs later behind the same messages | ADR-0016, S4 |
| F-S3 | Which model files and how they are treated | ANSWERED | ONNX only with a declared decoder; fine-tune only from our bases; never unpickle an upload | ADR-0017, S6 |
| F-S4 | How user models run in the pipeline | ANSWERED | `user:<id>` loaded on demand with an LRU of 3, ONNX only; benchmarks scored in process | ADR-0017, S5, S6 |
| F-S5 | Where registry state lives | ANSWERED | JSON manifests in the store plus Valkey index; Postgres with GEO-41 | ADR-0016, S1 |
| F-S6 | Labelling in scope | ANSWERED | No | ADR-0015 |
| F-S7 | Large uploads | ANSWERED | Direct browser to store, custom indah component, spike first | ADR-0015, S0, S3 |
| S1 | Single class and axis-aligned boxes only | ASSUMED | Matches every trainer and the benchmark; multi-class later needs per-class metrics | PLAN scope |
| S2 | Train presets, not hyperparameters (S-A2) | ASSUMED | `fast`, `balanced`, `accurate`, `best` from ADR-0014's recipes | ADR-0016 |
| S3 | Limits (S-A4) | ASSUMED | 5 GB and 20,000 images per dataset, 500 MB per model, one active training per tenant | ADR-0017, S7 |
| S4 | Split rule (S-A1) | ASSUMED | Roboflow-style folders as given, else 80/10/10 by sorted name | S3 |
| S5 | Security of uploads | ASSUMED | Zip guards, ONNX without external data, session from bytes, image-size guard; no public exposure | ADR-0017 |
| S6 | Public hosting, rate limits, GPU abuse | DEFERRED | Separate hosting milestone | PLAN out of scope |
| S7 | Remote GPU workers (RunPod) | DEFERRED | Same messages, later | ADR-0016 |
| S8 | Postgres registry | DEFERRED | With GEO-41 | ADR-0016 |
| S9 | Does SeaweedFS support bucket CORS for browser PUTs | OPEN, resolved by S0 | Unverified; fallback is streaming through the frontend | ADR-0015, S0 |
| S10 | Licences of new dependencies (onnx, Playwright, pycocotools in the trainer image) | ASSUMED | All Apache-2.0 or BSD from memory; each is checked by `make licences` and the PyPI metadata in the slice that adds it | S3, S7 |
