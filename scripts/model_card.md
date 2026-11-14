---
license: cc-by-sa-4.0
library_name: onnx
pipeline_tag: object-detection
base_model: Megvii-BaseDetection/YOLOX
datasets:
- spacenet
tags:
- object-detection
- building-detection
- remote-sensing
- yolox
- onnx
---

# YOLOX-tiny, buildings in overhead imagery

A one-class (`building`) YOLOX-tiny fine-tuned on SpaceNet 2 Khartoum (AOI 5), exported to ONNX, for the
CPU path of [geo-pipeline](https://github.com/leejianrong/geo-pipeline). Built for speed on a CPU, not for
the best accuracy: it is {speedup:.1f}x faster than a fine-tuned YOLOX-s and keeps {ratio:.0%} of its mAP@.5.

## Files

{files}

Input `1x3x416x416` float32, BGR, 0-255, letterboxed with grey (114) padding. Output is the raw head
`1x3549x6` (box offsets, log sizes, objectness, class probability); decoding and NMS are in
[`stages/detector/yolox_onnx.py`](https://github.com/leejianrong/geo-pipeline/blob/main/stages/detector/yolox_onnx.py).
The INT8 file is ONNX Runtime static QDQ quantisation, calibrated on 30 training tiles.

## Results

30 held-out SpaceNet Khartoum tiles (510 buildings), 640 px tiles, CPU, ONNX Runtime. "s/image" is the
model time for one 650 px image, which is 4 tiles.

{table}

The bar these models were held to: at least 90% of fine-tuned YOLOX-s's mAP@.5 and at least 2x faster
(INT8 within 2 points of its FP32 model). Full report:
[docs/benchmarks/lightweight-detector.md](https://github.com/leejianrong/geo-pipeline/blob/main/docs/benchmarks/lightweight-detector.md).

## Use

Copy the file to `data/models/{run}/yolox_buildings.onnx` (or `yolox_buildings_int8.onnx`) in a geo-pipeline
checkout and start the detector with `DETECTOR_MODELS=stub,yolox-tiny-buildings` (or
`yolox-tiny-buildings-int8`).

## Training

YOLOX's own trainer from the COCO checkpoint `yolox_tiny.pth`, one class, 416 px input, 60 epochs (the last
10 without mosaic), batch size 16, fp16, 240 training tiles: `make train-yolox VARIANT=tiny SIZE=416`. Made at
geo-pipeline commit `{commit}`.

## Limitations

- One city (Khartoum), 240 training tiles, one training run: the scores do not show how it generalises to
  other cities, sensors or resolutions.
- Targets are axis-aligned boxes converted from building footprints, so rotated buildings are overstated
  and small, touching buildings are hard (recall at IoU 0.5 is {recall:.2f}).
- Not for decisions about people or property without independent validation.

## Licence and attribution

CC BY-SA 4.0, because the training data is. Training data: SpaceNet on Amazon Web Services (AWS),
"Datasets", The SpaceNet Catalog, https://spacenet.ai/datasets/, CC BY-SA 4.0. Base model: YOLOX
(Megvii), Apache-2.0.
