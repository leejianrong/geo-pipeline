# Image vulnerabilities

See [distroless.md](distroless.md) for a runtime image with no High or Critical findings (the shell-free route for the base and for your own app).

`make scan` runs Trivy over our images (`geo-pipeline-*`) and fails on any HIGH or CRITICAL finding that has a fix. Build the images first. The published base images (`ghcr.io/leejianrong/geo-gdal`) are scanned by hand with `trivy image --ignore-unfixed` after each publish. Findings with no fix are not gated, because only the vendor can resolve them.

## State on 2026-11-16 (Trivy, HIGH and CRITICAL)

| Image | Before | After | Fixable after |
|---|---|---|---|
| api, frontend, trainer, detector, detector-slim | 49 to 52 HIGH | 47 HIGH | 0 |
| slicer, postprocess | 155 HIGH, 1 CRITICAL | 121 HIGH, 1 CRITICAL | 0 |
| postgres:17 (vendor) | 217 HIGH, 2 CRITICAL | 149 HIGH, 2 CRITICAL | 28 (gosu's Go stdlib, upstream) |
| valkey/valkey:8 (vendor) | 50 HIGH | 50 HIGH | 4 (apt, zlib1g; upstream) |
| chrislusf/seaweedfs (vendor) | 1 HIGH | 1 HIGH | 1 (`golang.org/x/net` 0.62; upstream) |

What changed:
- Every stage Dockerfile now runs `apt-get upgrade` (zlib1g, libpq5 and similar fixes Debian had published since the base image was built); the `python:3.12-slim` ones also upgrade `setuptools` and `wheel`.
- The default GDAL moved from 3.12 to 3.13 (`base/matrix.json`, 3.13.3) for CVE-2026-49014 (netCDF driver, fixed in 3.13.1).

What is left:
- The remaining HIGH findings in our images are Debian packages (util-linux, krb5, libxml2 and others) that Debian has not fixed. The one CRITICAL, libxml2 CVE-2026-6653, has no fix yet. Re-run `make scan` after rebuilding with `--pull` to pick up fixes as Debian releases them.
- The vendor images are pulled as `:17`, `:8` and `:latest`; pulling again takes whatever upstream has published. We do not rebuild them.

## Base images on GHCR (`geo-gdal`)

Scanned from the registry (`trivy image --image-src remote`) on 2026-11-17, after the republish with GDAL 3.13 and the pip, setuptools and apt upgrades. All severities, then only those with a fix:

| Tag | Critical | High | Medium | Low | With a fix (C / H / M / L) |
|---|---|---|---|---|---|
| `py3.12`, `py3.13`, `gdal3.13-*` | 1 | 121 | 157 | 309 | 0 / 1 / 1 / 1 |
| `gdal3.12-*`, `gdal3.11-*` | 1 | 122 | 151 | 310 | 0 / 2 / 1 / 3 |

- Before the fix, the default tags had 5 fixable High findings (vendored `msgpack`, `urllib3` and `setuptools` from the Python image, plus libpq5). The one left is `setuptools 70.3.0` (CVE-2025-47273): Trivy reads it from a lower layer of the Python base image, and the final filesystem only has 84.x.
- The 3.11 and 3.12 tags also carry GDAL CVE-2026-49014 (netCDF driver, fixed only in 3.13.1 and later). Use the 3.13 tags if that matters.
- About 95% of the findings have no fix: Debian packages that Debian has not patched, among them libxml2 CVE-2026-6653 (the one Critical). [distroless.md](distroless.md) removes most of them: 0 Critical, 0 High.
- Scan with `--image-src remote`: a local Docker with the containerd image store can lose layers of multi-platform images, and Trivy then fails.
