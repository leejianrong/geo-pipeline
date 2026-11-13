# geo-pipeline

Object detection on large GeoTIFFs: slice, detect (RF-DETR / YOLOX), merge, and write georeferenced GeoJSON.
Runs locally with Docker Compose; designed to grow into a hosted service.

**Status:** V1 (base images, stub-detector round trip) implemented; V2 onward planned. See [`docs/PLAN.md`](docs/PLAN.md), [`docs/SLICES.md`](docs/SLICES.md) and [`docs/adr/`](docs/adr).

## Try it

```bash
make e2e   # builds images, starts the stack, runs the round-trip test; output in data/out/
```

## GDAL + Python + numpy 2 base images

`base/Dockerfile` builds GDAL (Debian trixie, 3.10) with Python 3.12 or 3.13 and numpy 2.x:

```bash
docker build --build-arg PY=3.12 -t geo-gdal:py3.12 base/
docker run --rm geo-gdal:py3.12   # python 3.12.x numpy 2.x gdal 3.10.x OK
```

## Licence

Apache-2.0. The stack deliberately avoids AGPL components (ADR-0009).
