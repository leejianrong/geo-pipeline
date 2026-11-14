# ADR-0014: Lightweight CPU detector: YOLOX-nano/tiny, RF-DETR Nano, INT8

- Status: Accepted
- Date: 2026-11-14
- Deciders: Jian, Claude

## Context

On CPU, YOLOX-s takes about 65 ms per tile and RF-DETR Small about 180 ms (forward pass, 4 threads). The goal is a model that runs well on a CPU by choice, without AGPL code (ADR-0009). Measurements and the full question list are in `QUESTIONS.md` (L-series).

## Decision

- **Add, as opt-in backends** behind the existing registry (ADR-0004): YOLOX-nano and YOLOX-tiny (ONNX, same decoder, 416 px native), RF-DETR Nano, and INT8 variants of the YOLOX models. RF-DETR Small stays the quality choice and the default model; a "fast" preset is documented, not forced.
- **Success bar.** A candidate ships only if, fine-tuned on SpaceNet buildings, it reaches at least 90% of fine-tuned YOLOX-s's mAP@.5 (0.586, so 0.53 or better) at least 2x faster per tile on CPU. An INT8 model is kept only if it stays within 2 points of its own FP32 model. SpaceNet decides; COCO-200 is a sanity check only. Models that miss the bar are removed, not kept as dead options.
- **Hardware.** x86-64 only for now. Speed is measured at 1 and 4 threads as a proxy for small servers. ARM is deferred (the GDAL base image is amd64-only, and INT8 behaviour differs by CPU).
- **Pipeline overhead is in scope and goes first.** The slicer's PNG encode (56 ms of about 72 ms per tile at the default level) caps the pipeline at about 13.5 tiles/s, so faster models show no end-to-end gain until it is fixed. Tiles are written uncompressed (PNG level 0, 17 ms), which amends ADR-0002's format only in compression level. `DETECTOR_THREADS` (default 4, measured: more is no faster) and detector replicas provide throughput.
- **Torch-free image last.** The CPU path already runs on ONNX Runtime; an ONNX-only detector image (RF-DETR exported to ONNX) drops the 3.3 GB torch image to about 0.5 GB.
- **Publishing.** Fine-tuned weights that pass the bar go to Hugging Face (model cards, versioned files), not to GitHub releases; the repo keeps the scripts and links to them. Licence CC BY-SA 4.0 with SpaceNet attribution: the base models are Apache-2.0, but whether weights trained on CC BY-SA data are a derivative of it is unsettled, so the cautious reading is used. Slice L6.
- **Quantisation** is ORT static QDQ INT8, per-channel, calibrated on tiles from the target data. Dynamic quantisation does not speed up convolutions.
- **Input size** is decided from data: nano and tiny are tried at 416 and at 640.

## Measured after L1 (2026-11-14)

Stub model, real imagery, 48 tiles: 13.5 tiles/s before; 22.6 with PNG level 0 (slicer 43 ms/tile; encode 17, upload 12); 33.5 after encode and upload moved onto 4 threads (slicer 18 ms/tile); 48 with two detector replicas. GDAL reads stay on the slicer's main thread.

## Measured after L2 (2026-11-14, COCO-200, zero-shot)

mAP@[.5:.95] and model time per image: RF-DETR Small 0.566, 0.18 s; RF-DETR Nano 0.534, 0.10 s; YOLOX-s 0.440, 0.07 s; YOLOX-tiny 0.348, 0.02 s; YOLOX-nano 0.303, 0.01 s. All five are registered; slice L3 applies the bar on SpaceNet and removes those that miss it. The YOLOX backend reads its input size from the ONNX model.

## Verdict (L3, 2026-11-14, SpaceNet 30 held-out tiles, 4 threads)

Kept: `yolox-tiny-buildings` (416 px input; mAP@.5 0.564, 3.1x faster than YOLOX-s at 4 threads and 3.8x at 1); the INT8 work in L4 starts from it. Input size: 416 beat 640 for tiny on speed and on mAP@.5. Removed: YOLOX-nano (0.508 and 0.471; recall too low) and YOLOX-tiny at 640 (1.6x faster only), including the nano COCO backend and weights. RF-DETR Nano (0.596, 1.8x faster than RF-DETR Small, but slower than YOLOX-s) fails the speed rule as written against YOLOX-s; it is kept as the small quality-leaning model, pending a decision. Full table: `docs/benchmarks/spacenet-khartoum.md`.

## Excluded

| Candidate | Why |
|---|---|
| DEIMv2 | Newest tiny detector, but its LICENSE.md forbids commercial use (hosted service) |
| YOLOv6, 7, 9 | GPL |
| YOLOv10, 11, 12 | AGPL (Ultralytics) |
| PicoDet, NanoDet, D-FINE, RTMDet | Different toolchains, no expected gain over the YOLOX family here; revisit only if the chosen set misses the bar |

## Consequences

- The YOLOX decoder takes its input size and grid from the model instead of a fixed 640.
- Each shipped model needs a SpaceNet fine-tune, so weights stay out of git (`data/models/`).
- A second detector image exists after the last slice; both are built in CI.
