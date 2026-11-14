# ADR-0012: GPU detector as a build-arg variant and a compose override

- Status: Accepted
- Date: 2026-11-14
- Deciders: Jian, Claude

## Context

The CPU detector image uses CPU-only torch (3.3 GB). Fine-tuning and larger rasters need the local RTX 3080 Ti, which works in WSL and Docker.

## Decision

- One `stages/detector/Dockerfile`. Build args `TORCH_INDEX` and `ORT` pick CPU or CUDA wheels; `compose.gpu.yaml` overrides the `detector` service (cu128 torch, `onnxruntime-gpu`, GPU reservation, `DETECTOR_DEVICE=cuda`). `make up-gpu` uses it. The CPU and GPU detectors must not run together: they share a consumer group, so tiles would split between them.
- `DETECTOR_DEVICE` is `cpu` (default) or `cuda`. Asking for `cuda` without a visible GPU stops the container at startup with an explicit message; there is no silent CPU fallback. It applies to RF-DETR (torch) and YOLOX (ONNX Runtime CUDA provider).
- `onnxruntime-gpu` is pinned to 1.23.2: later releases link CUDA 13, while torch cu128 ships the CUDA 12 libraries, and `ort.preload_dlls()` then finds them in the torch wheels instead of a second CUDA install.

## Alternatives considered

| Option | Why not |
|---|---|
| Second Dockerfile or service for GPU | Two copies to keep in step; the override changes only what differs |
| `auto` device | Hides a missing GPU, which then looks like a slow run |
| Batching tiles in the detector now | Needs the consume loop to hold several messages. The measurements below show the detector is no longer the bottleneck on the GPU, so it would not raise throughput |

## Measured (`make bench-device`, 2026-11-14)

A 3840x2880 mosaic of one COCO photo (48 tiles of 640 px, 20% overlap, real objects on every tile), RTX 3080 Ti Laptop GPU vs this machine's CPU, one detector worker, one run each after a warm-up job:

| model | CPU tiles/s | GPU tiles/s | detector ms/tile, CPU | GPU |
|---|---|---|---|---|
| RF-DETR Small | 4.8 | 12.0 | 179 | 27 |
| YOLOX-s | 9.6 | 12.0 | 76 | 13 |

Detections are identical in practice: every feature has a same-class partner at IoU >= 0.9 with a score within 0.05 (136/136 for RF-DETR, 167/167 for YOLOX). Both GPU runs end at the same 12.0 tiles/s, which means the rest of the pipeline (slicing, PNG and S3 transfers, one message at a time) is the limit now, not the model. That is a single run on a small job, so treat the ceiling as approximate. Batching in the detector will not help until that is addressed.

## Consequences

The GPU image is several times larger than the CPU one because it carries the CUDA libraries (see the README for the size). CI has no GPU, so the GPU path is checked by `make bench-device` on a GPU host, not in CI; only the device resolution logic is unit-tested.
