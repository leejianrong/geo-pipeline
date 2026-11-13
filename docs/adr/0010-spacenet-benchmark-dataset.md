# ADR-0010: Benchmark on SpaceNet building footprints

- Status: Accepted
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

The owner asked for a generic dataset that ships as `.tif` and lets us assess application performance, meaning it has ground truth. DOTA and DIOR are mostly non-georeferenced images; xView's licence is unverified. SpaceNet is distributed as georeferenced GeoTIFFs on AWS, with building footprint polygons. Searches indicate CC BY-SA 4.0 and a public AWS bucket; sources conflict on band layout (SpaceNet 2 reported as 16-bit, 3 or 8 bands, ~30 cm; SpaceNet 7 as ~4 m Planet imagery).

## Decision

Use SpaceNet buildings (start with a SpaceNet 2 AOI such as Vegas) as the benchmark. Convert footprint polygons to axis-aligned ground-truth boxes in map coordinates. Score by IoU matching in map space: precision, recall, F1, mAP@0.5, plus latency per megapixel. Data is downloaded to `data/` and never committed (CC BY-SA applies to the data and derivatives). V5 begins by inspecting one file with `gdalinfo` and confirming licence and path before any download.

## Alternatives considered

| Option | Why not |
|--------|---------|
| DOTA / DIOR | Mostly plain images, so the GeoTIFF and georeferencing path goes untested; oriented labels |
| xView | GeoTIFF, but licence unverified here |
| Hand-made synthetic only | Already used for unit tests; says nothing about real model performance |

## Consequences

Real georeferenced ground truth and a 16-bit multi-band input that exercises the stretch path. A single-class `building` task means COCO weights are useless and we need a fine-tune (V5). Small dense buildings at tile seams are a hard case for NMS, which is useful for testing R3.
