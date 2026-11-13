# geo-pipeline

GeoTIFF in S3 → slice → detect → georeferenced GeoJSON, as three workers on Valkey Streams.
Plan: `docs/PLAN.md`, build order: `docs/SLICES.md` (V1 done when its acceptance tests pass), decisions: `docs/adr/`.
Trust the code over the docs where they disagree, and fix the doc.

## Commands
- `make` lists targets. `make check` = lint + fast tests (no Docker; also the pre-push hook, install with `make hooks`).
- `make e2e` starts the compose stack and runs `tests/stack` inside the `tools` container. Artefacts land in `data/out/` (open in QGIS).
- `make base` builds and verifies `geo-gdal:py3.12` and `py3.13`. `make licences` fails on AGPL/GPL Python packages.

## Conventions
- One branch per slice, PR to `main`, CI green before merge.
- No AGPL anywhere (ADR-0009). Don't add a dependency without checking its licence.
- All job creation goes through `geopipe.jobs.submit_job`. Messages live in `geopipe.messages` and carry `tenant_id`.
- Stages talk only through Valkey and S3, never to each other. Pixels never go through Valkey.
- Test through the Valkey/S3 boundary. Pure logic goes in `tests/unit` (runs on the host); anything needing GDAL or the stack goes in `tests/stack`.
