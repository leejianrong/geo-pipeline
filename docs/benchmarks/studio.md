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


## A model fine-tuned in the studio (S4, GEO-70)

Run 2026-11-16: `scripts/gpu_train_run.py --dataset spacenet-khartoum --preset fast` through the
stack on the shared RTX 3080 Ti (queued with pueue). The `fast` preset is YOLOX-tiny 416, 60
epochs on the 240 training tiles; the run ended `done`, exported to ONNX and registered itself as
a model. It was then scored like the others (`POST /bench/runs`, in-process, 26 annotated tiles of
the 30-tile test split), and run once through the real detector as `user:<id>`. Raw numbers:
[studio/gpu-fine-tune.json](studio/gpu-fine-tune.json).

| model | mAP@[.5:.95] | mAP@.5 | P@.5 | R@.5 | F1@.5 | detections |
|---|---|---|---|---|---|---|
| YOLOX-tiny 416, trained in the studio (`fast`, 60 epochs) | 0.262 | 0.544 | 0.663 | 0.467 | 0.548 | 2596 |
| YOLOX-tiny 416, our earlier training (reference) | 0.270 | 0.564 | 0.712 | 0.465 | 0.562 | 2006 |

The studio run is 0.020 below the reference mAP@.5, inside the 0.03 the plan allows. Recall is
the same; precision is lower and it makes more detections at the 0.05 score cut. One training run
each, so a gap of this size is not shown to be real. The detector check was one pipeline job on one
test tile with the model chosen as `user:<id>`: 44 buildings found where the ground truth has 39.
