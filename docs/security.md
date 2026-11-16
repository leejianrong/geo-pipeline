# Image vulnerabilities

See [distroless.md](distroless.md) for a runtime image with no High or Critical findings (the shell-free route for the base and for your own app).

`make scan` runs Trivy over our images (`geo-pipeline-*`) and fails on any HIGH or CRITICAL finding that has a fix. Build the images first. Findings with no fix are not gated, because only the vendor can resolve them.

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
