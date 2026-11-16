---
license: cc-by-sa-4.0
library_name: onnx
pipeline_tag: object-detection
base_model: roboflow/rf-detr
datasets:
- spacenet
tags:
- object-detection
- building-detection
- remote-sensing
- rf-detr
- onnx
---

# RF-DETR Nano, buildings in overhead imagery

A one-class (`building`) RF-DETR Nano fine-tuned on SpaceNet 2 Khartoum (AOI 5), exported to ONNX, for the
CPU path of [geo-pipeline](https://github.com/leejianrong/geo-pipeline). It is the smaller of two RF-DETR
models we fine-tuned: {speedup:.1f}x faster than a fine-tuned RF-DETR Small and it keeps {ratio:.0%} of its
mAP@.5. It is slower than the YOLOX models of the same project, so take it when you want a transformer
detector with a smaller cost, not the fastest one.

## Files

{files}

Input `1x3x384x384` float32, RGB, scaled to 0-1 and normalised with the ImageNet mean and standard
deviation after a plain bilinear resize to a square. Outputs `dets` (`1x300x4`, normalised centre x, centre y,
width, height) and `labels` (`1x300x2`, logits for `building` and the unused no-object slot); decoding is the top
300 sigmoid scores over query and class, in
[`libs/geopipe/src/geopipe/rfdetr_onnx.py`](https://github.com/leejianrong/geo-pipeline/blob/main/libs/geopipe/src/geopipe/rfdetr_onnx.py).

## Results

30 held-out SpaceNet Khartoum tiles (510 buildings), 640 px tiles, CPU, ONNX Runtime. "s/image" is the
model time for one 650 px image, which is 4 tiles.

{table}

The bar this model was held to: at least {min_accuracy} of the mAP@.5 of the largest fine-tuned model of the
family (RF-DETR Small) and at least {min_speedup}x faster than it. Full report:
[docs/benchmarks/lightweight-detector.md](https://github.com/leejianrong/geo-pipeline/blob/main/docs/benchmarks/lightweight-detector.md).

## Use

Copy the file to `data/models/{run}/rfdetr-nano.onnx` in a geo-pipeline checkout and start the torch-free
detector (`make up-slim`) with `DETECTOR_MODELS=stub,rfdetr-nano-buildings`. Or upload it in the Train tab's
model upload (decoder `rfdetr`, class `building`) and run it as one of your own models.

## Training

RF-DETR's own trainer from the COCO checkpoint `rf-detr-nano.pth`, one class, 384 px input, 30 epochs, learning
rate 1e-4, 240 training tiles: `make train-rfdetr VARIANT=Nano`, then `make export-rfdetr RUN={run} VARIANT=Nano`.
Made at geo-pipeline commit `{commit}`.

## Limitations

- One city (Khartoum), 240 training tiles, one training run: the scores do not show how it generalises to
  other cities, sensors or resolutions.
- Targets are axis-aligned boxes converted from building footprints, so rotated buildings are overstated
  and small, touching buildings are hard (recall at IoU 0.5 is {recall:.2f}).
- Not for decisions about people or property without independent validation.

## Licence and attribution

CC BY-SA 4.0, because the training data is. Training data: SpaceNet on Amazon Web Services (AWS),
"Datasets", The SpaceNet Catalog, https://spacenet.ai/datasets/, CC BY-SA 4.0. Base model: RF-DETR
(Roboflow), Apache-2.0.
