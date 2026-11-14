# ADR-0017: User models are ONNX only, loaded on demand, scored in process

- Status: Accepted
- Date: 2026-11-15
- Deciders: Jian, Claude

## Context

People will bring their own models. The detector today loads a fixed list of backends at startup (ADR-0004, `DETECTOR_MODELS`). A PyTorch `.pth` file is a pickle, so loading an uploaded one runs arbitrary code. ONNX files can also reference external data files by path.

## Decision

- **Accepted model upload: ONNX only**, with a declared decoder, `yolox` (raw head, as `yolox_onnx.py` decodes) or `rfdetr` (`dets` and `labels` outputs, as `rfdetr_onnx.py` decodes), plus class names. We ship scripts to export from YOLOX and RF-DETR checkpoints. **We never unpickle an uploaded file.**
- **Validation on upload:** the file must parse; it must have no external-data tensors (they can read arbitrary paths); input and output shapes must match the declared decoder; size at most 500 MB. The session is created from the bytes in memory, so no path from the file is ever opened.
- **Fine-tuning always starts from our own bases** (YOLOX-tiny, YOLOX-s, RF-DETR Nano, RF-DETR Small), never from an uploaded checkpoint.
- **On-demand loading in the detector:** a job's `model` of the form `user:<id>` makes the detector fetch `tenants/<tid>/models/<id>/model.onnx`, build an ONNX Runtime backend and keep it in an LRU of at most 3 user models. Built-in names behave as before. The tenant in the job must own the model (or the model is in `shared/`). Torch is not needed for this, so user models also run on the slim image (ADR-0014).
- **Benchmarks score in process** in the trainer stage: it runs the model over the chosen split with ONNX Runtime, applies the same decoder and NMS as the pipeline, and scores with the same code as `geopipe bench` (`score_model`). This is faster than one pipeline job per image and does not need the detector at all. The pipeline path is used for "try it on a raster" in the Detect tab.
- **Limits** (assumed, configurable): dataset at most 5 GB and 20,000 images, zip extraction guards against path traversal and size bombs, `PIL.Image.MAX_IMAGE_PIXELS` stays on.

## Alternatives considered

| Option | Why not |
|---|---|
| Accept `.pth` or `.pt` checkpoints | Arbitrary code execution on upload; a hosted service cannot do this |
| Sandboxed unpickling | Weaker than not unpickling, and a large effort for a convenience; exporting is a one-line script |
| Restart the detector with a new `DETECTOR_MODELS` per model | Interrupts everyone's jobs and needs operator access |
| Benchmark through the pipeline per image | Slow (one job and a GeoTIFF wrap per image); the in-process path scores the same boxes |
| Any ONNX detector layout | Each layout needs its own decoder; two are supported and more are added deliberately |

## Consequences

- The `BACKENDS` registry gains a fallback path for `user:<id>` names; unknown plain names still fail at startup as before.
- A user model that is not YOLOX or RF-DETR shaped is rejected with a message naming the expected outputs, not run.
- In-process scoring and pipeline scoring can differ slightly (the pipeline tiles and merges); the Explore view says which one produced a number.
