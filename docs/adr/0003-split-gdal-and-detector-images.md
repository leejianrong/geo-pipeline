# ADR-0003: Slicer and postprocess use the GDAL image; the detector has its own GDAL-free image

- Status: Accepted
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

The GDAL base images (ADR-0007) support Python 3.12 and 3.13. Torch, `rfdetr` and ONNX Runtime wheel availability on 3.13 is unverified. Detector images are large and may need CUDA later.

## Decision

Three images. Slicer and postprocess build on `geo-gdal:py3.12` (3.13 supported and tested). The detector builds on a plain `python:3.12-slim` base, with no GDAL, since it only sees PNG tiles (ADR-0002). Its Python version moves to 3.13 only after the ML wheels are verified.

## Alternatives considered

| Option | Why not |
|--------|---------|
| One image for all stages | Couples GDAL's Python version to ML wheels; bloats stages that don't need torch |
| Install torch into the GDAL image | Risks Python/numpy ABI conflicts and a huge image |

## Consequences

Each image pins its own Python and scales independently. `libs/geopipe` must install on both 3.12 and 3.13, so it stays pure Python with few dependencies.
