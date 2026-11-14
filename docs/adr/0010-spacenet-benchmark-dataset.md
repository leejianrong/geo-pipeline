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
- **Tile format (verified 2026-11-14 on 300 downloaded tiles):** 650x650, 3 bands, UInt16, EPSG:4326, about 0.3 m pixels, north-up, so the 16-bit stretch path is exercised. Value range per tile is roughly 0 to 1200 with zero-filled gaps. Footprints are GeoJSON polygons in lon/lat (CRS84, with a z of 0), so a vertex maps to a pixel through the inverse geotransform alone. 33 of the 300 Khartoum tiles have no buildings; the other 267 have 7,766 between them (up to 174 per tile, median box about 45 px wide).
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
- V5b reuses the scoring harness; the loader (`geopipe.spacenet`) converts footprints to boxes, writes 8-bit PNGs stretched like the pipeline does for training, and keeps the original GeoTIFFs for the held-out benchmark. The 300 tiles are split by tile number into 240 train, 30 valid and 30 test (contiguous blocks, to keep held-out tiles away from training neighbours). All tiles come from one city, so results do not show generalisation to other places.
- The COCO-pretrained models have no `building` class, so before fine-tuning they score 0 by construction (checked: 0 detections on the test tiles); there is no meaningful baseline.
