# SpaceNet Khartoum buildings: fine-tuned RF-DETR Small and YOLOX-s

| model | mAP@[.5:.95] | mAP@.5 | AR@100 | P@.5 | R@.5 | F1@.5 | median latency s/img | mean detect s/img | detections |
|---|---|---|---|---|---|---|---|---|---|
| rfdetr-buildings | 0.327 | 0.659 | 0.485 | 0.627 | 0.657 | 0.642 | 1.07 | 0.85 | 7794 |
| yolox-buildings | 0.303 | 0.586 | 0.467 | 0.689 | 0.551 | 0.612 | 0.61 | 0.31 | 1922 |

- Detections were collected at confidence >= 0.05 (needed for mAP). P, R and F1 use detections
  with score >= 0.5 and IoU >= 0.5.
- Latency is client-observed, from submit to done, through the whole pipeline (S3, queue, slicer,
  detector, postprocess), one image at a time. "detect" is the model's own time.
- One city (Khartoum); the models were fine-tuned on 240 tiles of the same city, so this does not show generalisation to other places.

![precision-recall](spacenet-khartoum/pr_curves.png)
![iou and latency](spacenet-khartoum/latency_iou.png)

## How this was produced

- Data: 300 tiles of SpaceNet 2 AOI 5 (Khartoum), 650x650 16-bit PS-RGB, split by tile number into 240 train, 30 valid and 30 test (`make spacenet-data`). Test is the 30 held-out tiles scored above, 510 buildings.
- RF-DETR Small: `make train-rfdetr`, from the COCO checkpoint, single class `building`, 30 epochs, batch size 8, about 7 minutes on the RTX 3080 Ti Laptop. Best validation mAP (EMA weights) 0.242.
- YOLOX-s: `make train-yolox`, YOLOX's own trainer from the COCO checkpoint, one class, 60 epochs (last 10 without mosaic), batch size 16, fp16, about 12 minutes. Validation AP@[.5:.95] about 0.24 at the end. Exported to ONNX (raw head, 1 class) and run by the same ONNX Runtime backend as the COCO model.
- Scoring: `make bench-spacenet` runs the held-out GeoTIFFs through the whole pipeline (CPU detector, one image at a time) and scores the GeoJSON pixel boxes against boxes converted from the footprints. Both models see identical inputs.
- The validation numbers are on the 30 valid tiles; the table is on the 30 test tiles, so they are not comparable.

## Reading the numbers

- Before fine-tuning the COCO weights have no `building` class, so the score was 0 by construction; the fine-tune is what makes either model useful here.
- RF-DETR is ahead on mAP and recall (0.327 vs 0.303 mAP@[.5:.95]; recall 0.66 vs 0.55 at IoU 0.5). YOLOX is more precise at the 0.5 score cut (0.69 vs 0.63) and about 2.7x faster in the detector (0.31 s vs 0.85 s per 650 px tile, CPU), which is the same trade as on COCO. YOLOX also emits far fewer low-score boxes (1922 vs 7794 at confidence 0.05), so its precision-recall curve ends earlier.
- Footprints are converted to axis-aligned boxes, which overstates the area of rotated buildings, and many Khartoum buildings are small, touching or nested. That caps IoU, and mAP@[.5:.95] is the harder number.
- One city, one training run per model, 30 test tiles: the differences between models are small enough that a different split or seed could change the order of some metrics. The numbers show that the pipeline and the fine-tunes work on overhead imagery, not how well they generalise to other cities or sensors.
- The tiles are CC BY-SA 4.0 and are not in the repo; only these metrics and plots are.
