# geo-pipeline

GeoTIFF in S3 → slicer → detector → postprocess → georeferenced GeoJSON, as three workers on Valkey Streams plus an S3-API object store (SeaweedFS), run with Docker Compose. It is meant to become a hosted service later, so contracts carry `tenant_id` and stay stateless.

Build order and acceptance tests: `docs/SLICES.md` (its end-to-end lines are the acceptance criteria). Plan: `docs/PLAN.md`. Decisions: `docs/adr/`. Status: V1-V3 merged; next V3b (visualisation), then V4. Work is tracked on the Pandan board `GEO` (`pandan board use GEO`); move cards as slices land. Trust the code over the docs where they disagree, and fix the doc.

## Layout
- `libs/geopipe/`: shared contracts and helpers. Pure Python that must install on 3.12 and 3.13.
- `stages/{slicer,detector,postprocess}/`: one `Dockerfile` and `main.py` each. Slicer and postprocess use the GDAL image; the detector does not have GDAL.
- `base/`: the `geo-gdal` image (GDAL + Python 3.12/3.13 + numpy 2), published to GHCR by CI.
- `tests/unit/` runs on the host; `tests/stack/` runs inside the `tools` container against the compose stack.

## Commands
- `make check`: lint and unit tests, no Docker. This is the pre-push hook (`make hooks` installs it).
- `make e2e`: stack plus stack tests (Docker; first build is slow because of torch). `make bench-large`: slow 2 GB benchmark, not in CI.
- `make base`, `make licences`, `make down`. Bare `make` lists targets.

## Conventions
- One branch per slice or fix, PR to `main`, CI green before merge.
- No AGPL anywhere (ADR-0009); check a dependency's licence before adding it.
- Jobs are created only through `geopipe.jobs.submit_job`. Messages live in `geopipe/messages.py` and carry `tenant_id` and `schema_version`.
- Stages talk only through Valkey and S3, never to each other. Pixels never go through Valkey.
- Test through the Valkey/S3 boundary. Update README, `docs/` and this file in the same PR as the change.

## Traps that already cost time
- A GDAL dataset must be released (`ds = None`) before its file is uploaded or read; until then the GeoTIFF is incomplete on disk.
- GDAL caches `/vsis3/` listings per process; the slicer clears the cache per job. Its block cache is capped by `GDAL_CACHEMAX` in `compose.yaml` (the default scales with host RAM).
- S3 `DeleteObjects` takes at most 1000 keys per call.
- Job-status counters must be written before the message that lets the job finish, or they race.
- `docker compose run` does not rebuild images; use `--build` (`make e2e` does).
- Host tests need `uv ... --with-editable ./libs/geopipe` (the Makefile does); a cached non-editable build hides new modules.
- In shell loops, `docker compose exec` needs `-T </dev/null` or it eats the loop's stdin. Never `pkill -f` with a pattern that appears in your own command line.
- Subagent `isolation: worktree` branches from the default branch, not the current one, and is deleted if unchanged. Create the worktree yourself when you need a specific commit.
