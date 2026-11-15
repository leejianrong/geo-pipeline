# Studio: the in-process benchmark against the pipeline benchmark

Run 2026-11-16 on the 30 held-out SpaceNet Khartoum tiles (510 buildings), CPU, 4 threads. Each
model file was uploaded through the Train tab's API (`POST /uploads/model`, `POST /models`, so
it went through the ONNX checks) and then scored with `POST /bench/runs`: the trainer cut each
image into the pipeline's tiles, ran the model, merged across seams and scored with
`bench.score_model`. The pipeline numbers are `make bench-spacenet`
([spacenet-khartoum.md](spacenet-khartoum.md)). Raw numbers:
[studio/inprocess-vs-pipeline.json](studio/inprocess-vs-pipeline.json).

| model | scorer | mAP@[.5:.95] | mAP@.5 | AR@100 | P@.5 | R@.5 | F1@.5 | detections |
|---|---|---|---|---|---|---|---|---|
| YOLOX-s 640 | pipeline | 0.303 | 0.586 | 0.467 | 0.689 | 0.551 | 0.612 | 1922 |
| YOLOX-s 640 | in-process | 0.303 | 0.586 | 0.467 | 0.689 | 0.551 | 0.612 | 1922 |
| YOLOX-tiny 416 | pipeline | 0.270 | 0.564 | 0.436 | 0.712 | 0.465 | 0.562 | 2006 |
| YOLOX-tiny 416 | in-process | 0.270 | 0.564 | 0.436 | 0.712 | 0.465 | 0.562 | 2006 |

The two scorers agree on every number, down to the detection counts (the plan allowed 0.003 on
mAP@.5). They only agree because the in-process runner copies what `geopipe bench` does: tiles
with an overlap of 0 (which also switches off the rule that drops a box cut by an interior tile
edge) and the images `pick_images` selects (images without annotations are left out). A first
version that used the pipeline's default overlap of 0.2 scored YOLOX-s at 0.600 mAP@.5 and
YOLOX-tiny at 0.571: the same model, a different merge rule.

Speed is not comparable: the pipeline's latency includes the queue, the slicer and postprocess, the
in-process one is the model alone (0.75 s per image for YOLOX-s, 0.27 s for tiny, one run each
on a shared machine, so treat them as rough).
