# ADR-0007: Build our own GDAL base images for Python 3.12 and 3.13 with numpy 2.x

- Status: Accepted (replaces "pin the OSGeo ubuntu-small image")
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

A main goal is a container image with GDAL, Python 3.12 or 3.13, and numpy 2.x. The official `ghcr.io/osgeo/gdal:ubuntu-small-latest` (checked 2026-11-13) is a 3.14.0dev snapshot on Ubuntu 26.04 with Python 3.14.4 and numpy 2.3.5. GDAL bindings work with numpy 2 there, but the Python version cannot be chosen.

We then built `python:<ver>-slim-trixie` plus Debian's libgdal (3.10.3), compiled the GDAL Python bindings from PyPI against that exact version with numpy ≥ 2 installed, and ran a smoke test. Python 3.12.16 and 3.13.18 both pass with numpy 2.5.4. Single-stage images were 1.69 GB; the multi-stage `base/Dockerfile` brings them to about 743 MB.

## Decision

`base/Dockerfile` takes `ARG PY` (3.12 or 3.13) and builds `geo-gdal:py<ver>`: a builder stage compiles `GDAL==$(gdal-config --version)` into a wheel, the runtime stage installs `gdal-bin`, numpy `>=2,<3` and that wheel. `base/verify.py` is the image's default command and checks numpy ≥ 2, array round trip, `/vsis3/` support and the GTiff, COG and PNG drivers. CI builds and runs it for both versions. On every change to `base/` on `main`, a workflow publishes `ghcr.io/leejianrong/geo-gdal:py3.12` and `:py3.13` (plus immutable `py<ver>-gdal<ver>-numpy<ver>` tags), running `verify-gdal` before the push and again on the image pulled back from the registry.

## Alternatives considered

| Option | Why not |
|--------|---------|
| OSGeo official images | Python version fixed by the Ubuntu base (3.14 today) |
| conda-forge (micromamba) | Works in principle, not tested; heavier tooling and a separate package universe |
| Build GDAL from source | Slow and unnecessary while Debian ships a recent GDAL |
| Ubuntu base | 3.13 needs deadsnakes or a source build; 3.12 is native |

## Consequences

We control the Python version and numpy range. Debian trixie fixes GDAL at 3.10.x; newer GDAL needs a source or backports build. The bindings are compiled per release, so the bump process is "rebuild and run verify". Extra GDAL drivers beyond Debian's set are not included.
