# geo-pipeline

Object detection on large GeoTIFFs: slice, detect (RF-DETR / YOLOX), merge, and write georeferenced GeoJSON.
Runs locally with Docker Compose; designed to grow into a hosted service.

**Status:** V1 (base images, stub-detector round trip) implemented; V2 onward planned. See [`docs/PLAN.md`](docs/PLAN.md), [`docs/SLICES.md`](docs/SLICES.md) and [`docs/adr/`](docs/adr).

## Try it

```bash
make e2e   # builds images, starts the stack, runs the round-trip test; output in data/out/
```

## GDAL + Python + numpy 2 base images

Published on GHCR (public, amd64), rebuilt and verified whenever `base/` changes on `main`:

```bash
docker run --rm ghcr.io/leejianrong/geo-gdal:py3.12   # python 3.12.x numpy 2.x gdal 3.10.x OK
docker run --rm ghcr.io/leejianrong/geo-gdal:py3.13
```

Tags: `py3.12`, `py3.13`, and immutable `py<ver>-gdal<ver>-numpy<ver>` such as `py3.12-gdal3.10.3-numpy2.5.4`.
The default command runs `verify-gdal`, which checks array round trips for six dtypes, `OpenArray` memory sharing, `/vsis3/` and the GTiff, COG and PNG drivers.

To build locally instead: `docker build --build-arg PY=3.12 -t geo-gdal:py3.12 base/` (or `make base` for both).

## Licence

Apache-2.0. The stack deliberately avoids AGPL components (ADR-0009).
