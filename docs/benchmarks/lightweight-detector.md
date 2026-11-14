# Lightweight CPU detector: results of the L-series

One page that holds every number from slices L1 to L5 (ADR-0014), with the raw `results.json` files in [`lightweight-detector/`](lightweight-detector/). Machine: i9-12900H laptop, CPU only, 2026-11-14. Details of each run: [COCO](coco-val-200.md), [SpaceNet](spacenet-khartoum.md). "Detect s/img" is model time for one 650 px image (4 tiles); the 1 and 4 thread columns are `DETECTOR_THREADS`.

## L1: pipeline overhead (stub model, real imagery, 48 tiles)

| setup | tiles/s |
|---|---|
| before (PNG level 6, serial encode) | 13.5 |
| PNG level 0 | 22.6 |
| plus encode and upload on 4 threads | 33.5 |
| plus a second detector replica | 48 |

## L2: COCO-200, zero-shot (sanity check only)

Raw: `coco200-l2.json`. mAP@[.5:.95] and model time per image: RF-DETR Small 0.566 (0.18 s), RF-DETR Nano 0.534 (0.10 s), YOLOX-s 0.440 (0.07 s), YOLOX-tiny 0.348 (0.02 s), YOLOX-nano 0.303 (0.01 s; removed after L3).

## L3 and L4: SpaceNet buildings, fine-tuned, 30 held-out tiles

Raw: `spacenet-l3-*.json` (first seven rows), `spacenet-l4-*.json` (the INT8 rows).

| model | mAP@.5 | F1@.5 | detect s/img, 4 threads | 1 thread | vs YOLOX-s speed (1 thread) | bar |
|---|---|---|---|---|---|---|
| rfdetr-buildings (Small) | 0.659 | 0.642 | 0.84 | 2.55 | 0.4x | reference |
| rfdetr-nano-buildings | 0.596 | 0.602 | 0.43 | 1.29 | 0.7x | accuracy only; kept pending a decision |
| yolox-buildings (s) | 0.586 | 0.612 | 0.29 | 0.95 | 1x | reference |
| yolox-buildings-int8 | 0.573 | 0.588 | 0.12 | 0.33 | 2.9x | within 2 points of FP32 (-1.3) |
| yolox-tiny-buildings (416) | 0.564 | 0.562 | 0.09 | 0.26 | 3.7x | passes |
| yolox-tiny-buildings-int8 | 0.559 | 0.548 | 0.06 | 0.11 | 8.6x | within 2 points of FP32 (-0.5) |
| yolox-tiny-buildings-640 | 0.546 | 0.566 | 0.19 | 0.58 | 1.6x | fails speed; removed |
| yolox-nano-buildings-640 | 0.508 | 0.509 | 0.07 | 0.15 | | fails accuracy; removed |
| yolox-nano-buildings-416 | 0.471 | 0.468 | 0.05 | 0.08 | | fails accuracy; removed |

The bar (ADR-0014): at least 0.527 mAP@.5 (90% of YOLOX-s's 0.586) and at least 2x faster than YOLOX-s; INT8 within 2 mAP points of its FP32 model. Timings for different rows come from separate runs of one machine with other workloads, so differences of about 10% are noise. The fine-tuned rows from the first and second SpaceNet run agree to the third decimal on mAP; speeds varied by up to 5%.

## End to end through the pipeline (one detector, 4 threads, 48-tile mosaic)

First run: YOLOX-s 10.1 tiles/s, YOLOX-tiny 416 18.6, RF-DETR Nano 7.1, RF-DETR Small 3.4. Second run (separate stack): YOLOX-s 10.8, YOLOX-s INT8 19.4, YOLOX-tiny 21.6, YOLOX-tiny INT8 27.9. The slicer (18 ms per tile) and the detector's fetch and decode steps set the floor, so the fastest models gain little more end to end. The torch-free image rows are added by L5.

## L5: torch-free detector image (ONNX Runtime only)

Raw: `spacenet-l5-slim-4threads.json`. Same model names, same weights, RF-DETR exported to ONNX (`make export-rfdetr` for fine-tunes).

| | default image (torch) | slim image (ONNX only) |
|---|---|---|
| image size (`docker images`) | 4.03 GB | 1.05 GB |
| model load, RF-DETR Nano, 4 threads | 5.6 to 6.2 s | 0.43 s |
| container start to first detection | 7.1 to 7.7 s | 1.1 to 1.2 s |
| SpaceNet mAP@.5, rfdetr-buildings | 0.659 | 0.659 |
| SpaceNet mAP@.5, rfdetr-nano-buildings | 0.596 | 0.596 |
| pipeline tiles/s, RF-DETR Nano (3 runs) | 5.6 | 5.4, 6.6 |
| pipeline tiles/s, RF-DETR Small (3 runs) | 3.0 | 3.3, 3.7 |
| cat photo, same job, 4 models | | boxes identical (IoU 1.0, scores equal at output precision) |

The estimate in the plan was about 0.5 GB; the image is 1.05 GB because it carries the two COCO RF-DETR exports (222 MB), YOLOX-s and tiny (56 MB), ONNX Runtime and its dependencies, and boto3. A first version of the ONNX backend resized with PIL, which antialiases and lost 0.9 mAP@.5 points for RF-DETR Small (0.650); the current plain bilinear resize, like torch's default, brings it back to 0.659 at a cost of about 10 to 16 ms per tile. Speeds differ by up to 10% between runs because the machine has other workloads, so "equal" means within that.

## Caveats

One city, 240 training tiles, 30 test tiles, one training run per model. These numbers do not show how the models generalise to other cities or sensors.
