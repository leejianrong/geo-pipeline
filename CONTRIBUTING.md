# Contributing

## Dev loop

```bash
make hooks   # once: runs lint and the fast tests before every push
make check   # lint + unit tests, no Docker
make e2e     # starts the stack, runs the stack tests (needs Docker)
make         # lists every target
```

`make bench-large` runs a slow 2 GB raster benchmark; it is not part of CI.

## Changes

- One branch per slice or fix, then a PR to `main`. CI (lint and unit tests, base-image build for Python 3.12 and 3.13, end-to-end, licence check) must be green before merge.
- Add a test for every bug you fix. Pure logic goes in `tests/unit`; anything that needs GDAL or the running stack goes in `tests/stack`.
- Do not add AGPL or GPL Python dependencies. `make licences` checks the images; the policy is in [ADR-0009](docs/adr/0009-no-agpl-object-store-seaweedfs.md).
- Update the README, `docs/` and `AGENTS.md` in the same PR as the change they describe.

Plans and decisions live in `docs/` (`PLAN.md`, `SLICES.md`, `adr/`). Benchmark data (SpaceNet) is CC BY-SA and must never be committed.
