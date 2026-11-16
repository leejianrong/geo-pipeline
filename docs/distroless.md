# Distroless runtime for GDAL, Python and numpy

`geo-gdal` is a normal Debian image: it has a shell, `apt` and `pip`, which is what you want to build on, but the packages you do not use still show up in vulnerability scans (see [security.md](security.md)). For the image you ship, install everything in a builder stage on `geo-gdal`, then copy only the finished result into a distroless base.

Working example: [`examples/distroless/`](../examples/distroless/) (`Dockerfile`, `collect-libs.sh`, a stand-in `your_app/main.py`).

```
docker build --build-arg PY=3.13 -t my-app examples/distroless      # or PY=3.12
docker run --rm my-app
```

## What the build does

1. **Builder stage** (`FROM ghcr.io/leejianrong/geo-gdal:py<PY>`): `pip install` your requirements here, remove `pip`, `setuptools` and `wheel`, and copy your code in.
2. **Collect the shared libraries.** `collect-libs.sh` runs `ldd` over libgdal, the interpreter and every extension module and copies each library they need, keeping its path. The C library is left out because the base has it.
3. **Final stage** (`FROM gcr.io/distroless/cc-debian13:nonroot`): copy the libraries, `/opt/gdal`, PROJ's data, the interpreter and `/usr/local/lib` (the standard library and `site-packages`, which already hold numpy and the GDAL bindings), and your code.
4. **Environment.** There is no `ldconfig`, so set `LD_LIBRARY_PATH=/usr/local/lib:/opt/gdal/lib`, plus `GDAL_DATA` and `PROJ_DATA`. The entry point is the interpreter in exec form (there is no shell to start it).

The same recipe works for Python 3.12 and 3.13, because the interpreter comes from the `geo-gdal` image rather than from the distroless base. `gcr.io/distroless/python3-debian13` would also run 3.13, with Debian's own Python, but that is 3.13.5 with unfixed CVEs in its packages, against 3.13.18 here, and there is no distroless Python 3.12 at all.

## Result

Trivy on the example, built on the 2026-11-16 base (all severities, with and without a fix):

| Image | Critical | High | Medium | Low | Size |
|---|---|---|---|---|---|
| `geo-gdal:py3.13` | 1 | 121 | 157 | 309 | 503 MB |
| distroless example, 3.13 | 0 | 0 | 43 | 12 | 367 MB |
| distroless example, 3.12 | 0 | 0 | 43 | 12 | 372 MB |

`base/verify.py` (numpy array to GDAL dataset round trips) passes in both, and the standard library's `ssl`, `sqlite3` and HTTPS work. CI builds the example on every change to `base/` or `examples/distroless/`.

## What you give up

- No shell and no package manager: `docker exec ... sh` fails. For debugging, run the builder stage (`--target build`), or use the `:debug` tag of the distroless base, which adds busybox.
- Compose or Kubernetes health checks cannot use shell commands. Use an HTTP probe, or `["/usr/local/bin/python", "-c", "..."]`.
- Anything your app calls as a program (`gdalinfo`, `ogr2ogr`, `curl`) is not there unless you copy it (and its libraries, by adding it to the `collect-libs.sh` call). The Python bindings do not need them.
- A missing library shows up at run time as an `ImportError` or `error while loading shared libraries`. If you add a package that loads a library with `dlopen` (not found by `ldd`), add the library's directory to the `collect-libs.sh` call.
- The image does not update itself: rebuild with `--pull` to pick up fixes.

## Not covered

The pipeline's own stage images (`stages/*`) still use the slim base. Moving them over is possible with this recipe, but their health checks and `docker compose exec` use would need to change first.
