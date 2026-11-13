# ADR-0007: Build our own GDAL base images (GDAL 3.11-3.13 × Python 3.12/3.13, numpy 2.x)

- Status: Accepted (replaces the earlier "pin the OSGeo ubuntu-small image" and "Debian's libgdal 3.10" versions of this ADR)
- Date: 2026-11-13
- Deciders: Jian, Claude

## Why we build our own image

We need a container image that has all of:

1. **Python 3.12 and Python 3.13** (a stated goal of the project),
2. **numpy 2.x** working with GDAL's Python bindings at runtime,
3. **GDAL 3.11, 3.12 or 3.13** (colleagues use 3.12 and prefer 3.11-3.13), on amd64.

**The official `ghcr.io/osgeo/gdal` Ubuntu images do not provide that combination.** We pulled the latest patch of each release (`ubuntu-small-3.11.5`, `-3.12.4`, `-3.13.3`) on 2026-11-13 and tested them:

| Official image | Ubuntu | Python | numpy in image | With numpy 2.x |
|---|---|---|---|---|
| `ubuntu-small-3.11.5` | 24.04 | 3.12.3 | 1.26.4 | **breaks**: after `pip install "numpy>=2"` (2.5.4), `import osgeo.gdal_array` fails with `ImportError: numpy.core.multiarray failed to import` |
| `ubuntu-small-3.12.4` | 24.04 | 3.12.3 | 1.26.4 | **breaks**, same error |
| `ubuntu-small-3.13.3` | 26.04 | 3.14.4 | 2.3.5 | works, but only on Python 3.14 |

So:

- For GDAL 3.11 and 3.12 the bindings in the official images were compiled against NumPy 1.x, so numpy 2 cannot be used with them.
- For GDAL 3.13 numpy 2 works, but only on Python 3.14.
- No official Ubuntu image has Python 3.13 (Ubuntu 24.04 ships 3.12, 26.04 ships 3.14), and Python cannot be chosen independently of the OS.
- The images ship no `pip`, so any customisation starts with installing it.

(`ubuntu-small-latest`, checked the same day, is a GDAL 3.14 dev snapshot on Ubuntu 26.04 with Python 3.14.4, which is outside the requested range as well.) The `ubuntu-full` and `alpine` variants share the same Ubuntu and Python constraints, and were not tested individually.

## Decision

Build GDAL from source and compile the matching Python bindings against numpy 2, for a matrix defined in `base/matrix.json`: GDAL 3.11.5, 3.12.4 (default) and 3.13.3, each on Python 3.12 and 3.13.

`base/Dockerfile` has three stages:

1. **gdal**: compiles libgdal from the release tarball on `debian:trixie-slim` into `/opt/gdal`, and records which Debian packages provide the shared libraries it links against. This stage does not depend on the Python version, so both Python variants reuse it.
2. **bindings**: on `python:<ver>-slim-trixie`, builds the PyPI `GDAL==<version>` bindings with numpy ≥ 2 installed. The bindings must match libgdal exactly.
3. **runtime**: `python:<ver>-slim-trixie` plus only the runtime libraries, `/opt/gdal`, numpy and the bindings wheel. No compilers.

`base/verify.py` is the image's default command and runs the numpy 2 checks: round trips for six dtypes, `OpenArray` memory sharing in both directions, windowed reads, `/vsis3/` and the GTiff, COG and PNG drivers. `base/build.sh` builds one combination and asserts the version it reports; `base/publish.sh` pushes it to GHCR, pulls it back and verifies it again.

Tags on `ghcr.io/leejianrong/geo-gdal`: `gdal<minor>-py<python>` (moving, for example `gdal3.12-py3.12`), `py<python>` (moving, points at the default GDAL, 3.12), and immutable `py<python>-gdal<full>-numpy<numpy>`.

CI (`ci-base.yml`) builds and verifies all six combinations when `base/` changes. `publish-base.yml` publishes them on merge to `main`.

## Alternatives considered

| Option | Why not |
|--------|---------|
| OSGeo official Ubuntu images | Fails the requirements, see the table above |
| Official image plus rebuilt Python bindings | Fixes numpy 2 for GDAL 3.11/3.12, but only on Ubuntu 24.04's Python 3.12; no Python 3.13 |
| Debian's packaged GDAL (our first version, 3.10.3) | Works with numpy 2 on both Pythons, but Debian trixie is fixed at GDAL 3.10 and cannot provide 3.11-3.13 |
| conda-forge (micromamba) | Could supply the matrix, but is a separate package universe and tooling; not tested |
| Ubuntu base with deadsnakes | A third-party PPA for Python 3.13, and still no GDAL 3.11-3.13 from apt |

## Consequences

- We control the GDAL version, the Python version and the numpy range, and can verify the combination ourselves.
- The images are smaller than the Debian-GDAL ones were: 472-485 MB across the matrix, against 743 MB. All six combinations (GDAL 3.11.5, 3.12.4, 3.13.3 on Python 3.12.16 and 3.13.18, numpy 2.5.4) pass `verify-gdal`. A full local build took about 25 minutes with the three GDAL versions in parallel.
- **We own the driver set.** The build enables the common libraries (GeoTIFF/COG, PNG, JPEG, WebP, JPEG2000, GEOS, PROJ, curl for `/vsis3/`, SQLite/GeoPackage, NetCDF, HDF5, PostgreSQL). The GDAL 3.12 build has 140 raster and 68 vector drivers, against 152 and 83 in the Debian 3.10 image. Missing among others: HDF4, FITS, HEIF/AVIF, LIBKML, MySQL, ODBC/MSSQL. Adding one means adding its `-dev` library to the Dockerfile.
- Compiling GDAL takes minutes per version, so CI uses one job per GDAL version and builds both Python variants in it. The application e2e job reuses the published image instead of compiling.
- A new GDAL patch release is a one-line edit in `base/matrix.json`, provided the matching `GDAL` bindings exist on PyPI.
- The `py3.12` and `py3.13` tags now point at GDAL 3.12 instead of 3.10.3. The earlier immutable tags, such as `py3.12-gdal3.10.3-numpy2.5.4`, stay published and unchanged.
- This ADR's conclusions about the official images are a snapshot of 2026-11-13; re-check them if OSGeo changes the base OS or the numpy build.
