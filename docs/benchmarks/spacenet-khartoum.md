# SpaceNet Khartoum buildings: fine-tuned RF-DETR Small

| model | mAP@[.5:.95] | mAP@.5 | AR@100 | P@.5 | R@.5 | F1@.5 | median latency s/img | mean detect s/img | detections |
|---|---|---|---|---|---|---|---|---|---|
| rfdetr-buildings | 0.327 | 0.659 | 0.485 | 0.627 | 0.657 | 0.642 | 1.02 | 0.80 | 7794 |

- Detections were collected at confidence >= 0.05 (needed for mAP). P, R and F1 use detections
  with score >= 0.5 and IoU >= 0.5.
- Latency is client-observed, from submit to done, through the whole pipeline (S3, queue, slicer,
  detector, postprocess), one image at a time. "detect" is the model's own time.
- One city (Khartoum); the models were fine-tuned on 240 tiles of the same city, so this does not show generalisation to other places.

![precision-recall](spacenet-khartoum/pr_curves.png)
![iou and latency](spacenet-khartoum/latency_iou.png)

## How this was produced

- Data: 300 tiles of SpaceNet 2 AOI 5 (Khartoum), 650x650 16-bit PS-RGB, split by tile number into 240 train, 30 valid and 30 test (`make spacenet-data`). Test is the 30 held-out tiles scored above, 510 buildings.
- Training: `make train-rfdetr`, RF-DETR Small from the COCO checkpoint, single class `building`, 30 epochs, batch size 8, on the RTX 3080 Ti Laptop GPU; about 7 minutes. Best validation mAP (EMA weights) was 0.242 on the 30 valid tiles (the test tiles are different tiles, so the two numbers are not comparable).
- Scoring: `make bench-spacenet`, which runs the held-out GeoTIFFs through the whole pipeline (CPU detector) and scores the GeoJSON pixel boxes against boxes converted from the footprints.

## Reading the numbers

- Before fine-tuning the COCO weights have no `building` class, so the score was 0 by construction; the fine-tune is what makes the model useful here.
- Footprints are converted to axis-aligned boxes, which overstates the area of rotated buildings, and many Khartoum buildings are small, touching or nested. That caps IoU, and mAP@[.5:.95] is the harder number.
- One city, one run, 30 tiles: the numbers show the pipeline and fine-tune work on overhead imagery, not how well the model generalises to other places or sensors. A YOLOX comparison is the next step.
- The tiles are CC BY-SA 4.0 and are not in the repo; only these metrics and plots are.
