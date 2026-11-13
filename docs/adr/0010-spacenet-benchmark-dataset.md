# ADR-0010: Benchmark on COCO now, SpaceNet building footprints later

- Status: Accepted (amended 2026-11-14: V5 split into a COCO slice now and a SpaceNet slice later)
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

The owner asked for a generic dataset that ships as `.tif` and lets us assess application performance, meaning it has ground truth. DOTA and DIOR are mostly non-georeferenced images; xView's licence is unverified. SpaceNet is distributed as georeferenced GeoTIFFs on AWS with building footprint polygons, so it matches the intended overhead use best.

SpaceNet needs a fine-tune first, because the shipped weights are trained on COCO (everyday objects) and do not find buildings from above. The owner chose to defer that and benchmark on COCO first, where both RF-DETR and YOLOX work out of the box and ground-truth boxes exist.

## Decision

1. **V5 (now): COCO val2017.** Wrap COCO images as GeoTIFFs (arbitrary CRS), run them through the real pipeline for each model, and score the GeoJSON against COCO's ground truth with `pycocotools` (mAP@[.5:.95], mAP@.5) plus precision, recall and F1 at IoU 0.5, and latency. This validates the scoring code and compares RF-DETR with YOLOX, but it measures everyday objects, not overhead imagery. A 200-image subset is enough; the full 778 MB `val2017.zip` is not needed.
2. **V5b (later): SpaceNet 2 buildings**, reusing the same harness, with a fine-tune of both models on this machine's GPU. The facts below were checked on 2026-11-14 without downloading any imagery.

### SpaceNet facts (checked)

- **Licence: CC BY-SA 4.0.** The bucket's own `LICENSE.md` says "The 'SpaceNet Dataset' is licensed under a Creative Commons Attribution-ShareAlike 4.0 International License" and covers everything in the bucket except the `Hosted-Datasets` folder. The SpaceNet 2 dataset page states the same and asks for a citation (Van Etten, Lindenbaum & Bacastow, 2018, "SpaceNet: A Remote Sensing Dataset and Challenge Series"). Share-alike means derived datasets must carry the same licence, so data stays in gitignored `data/` and is never committed.
- **Access: public, no AWS account needed in practice.** Bucket `s3://spacenet-dataset` (us-east-1). The AWS Open Data page gives `aws s3 ls --no-sign-request s3://spacenet-dataset/` and says no account is required; the SpaceNet pages say an AWS account is needed. We observed anonymous HTTPS listing and an anonymous `HEAD` of a 23.9 GB tarball both succeed (`200 OK`), so it is not requester-pays.
- **Layout allows a subset.** Per-city tarballs are 1.6-23.9 GB (smallest training one: Khartoum, 4.7 GB; Paris 5.3 GB). Individual tiles are also separate objects, for example `spacenet/SN2_buildings/train/AOI_5_Khartoum/PS-RGB/SN2_buildings_train_AOI_5_Khartoum_PS-RGB_img1.tif` (about 2.4 MB) with a matching `geojson_buildings/..._img1.geojson` (8-46 KB). Prefixes: `MS`, `PAN`, `PS-MS`, `PS-RGB`, `geojson_buildings`. A 300-tile training subset is about 750 MB.
- **Not yet verified:** bit depth, band count and CRS. The tile size is consistent with 650×650×3 at 2 bytes per pixel, so we expect 16-bit RGB, which would exercise our stretch path. Confirm with `gdalinfo` on one tile when the first one is fetched.
- **Fine-tuning hardware:** this machine has an RTX 3080 Ti Laptop GPU (16 GB), usable inside WSL and Docker. RF-DETR Small (about 32 M parameters) and YOLOX-s (about 9 M) fine-tune fully in that memory, so QLoRA (a technique for squeezing very large language models into small GPUs) is unnecessary, and no rented GPU is needed. A CUDA training image is required; our detector image has CPU-only torch.

## Alternatives considered

| Option | Why not |
|--------|---------|
| SpaceNet first | Needs a fine-tune before any number means anything; deferred by the owner |
| DOTA / DIOR | Mostly plain images, so the GeoTIFF and georeferencing path goes untested; oriented labels |
| xView | GeoTIFF, but licence unverified here |
| Hand-made synthetic only | Already used for unit tests; says nothing about real model performance |
| Skip benchmarking | Leaves "how accurate is it" unanswered and the YOLOX backend untested |

## Consequences

- COCO gives a real, reproducible RF-DETR vs YOLOX comparison immediately, at zero cost, and exercises the whole pipeline (S3, queue, tiling, georeferencing, NMS) with ground truth.
- It says nothing about overhead imagery. Do not read COCO numbers as a prediction for buildings seen from above.
- **Dataset images stay out of git, and must never be redistributed.** COCO's own annotation file lists per-image Flickr licences that include NonCommercial and NoDerivs variants (checked in `instances_val2017.json`: Attribution-NonCommercial-ShareAlike, Attribution-NonCommercial, Attribution-NonCommercial-NoDerivs and others). We fetch images at run time and use them only to evaluate; the tools write derived GeoTIFFs under gitignored `data/` and a scratch bucket, never into the repo or a public artefact. Only a 5-image *annotation* excerpt is committed as a test fixture (COCO annotations are CC BY 4.0, stated from memory and not re-verified here).
- V5b reuses the scoring harness unchanged apart from a SpaceNet loader (footprint polygons to boxes, 16-bit stretch).
