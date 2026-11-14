# ADR-0015: The studio is tabs in the existing indah frontend, through the API

- Status: Accepted
- Date: 2026-11-15
- Deciders: Jian, Claude

## Context

The owner wants a studio: one place to explore a dataset and the model trained on it (images with ground-truth and predicted boxes, metrics), and to bring your own dataset or model, fine-tune and benchmark. The frontend (ADR-0013) is an indah app that is a client of the HTTP API only. indah already has `Tabs`, `Gallery`, `Chart`, `Plot`, `Heatmap`, `MasterDetail`, `SplitPane`, `Stat`, `Steps`, `Upload`, `Progress`, `ImageOverlay` and `CustomComponent`.

## Decision

- **One app, tabs:** Detect (the current V8 screens), Explore, Train. No second web service. The browser still never holds Valkey or S3 credentials; everything goes through new API endpoints (datasets, models, runs, uploads).
- **Scope:** explore and evaluate datasets, fine-tune, benchmark. **No labelling or annotation editing**: people label in CVAT or Roboflow and bring the COCO export. One class, axis-aligned boxes.
- **Build order:** Explore first (read-only, uses data we already produce), then dataset upload, then training, then bring-your-own models and benchmarks (slices S0 to S7 in `SLICES.md`; S0 is a short spike on direct upload and CORS, run first because it is the riskiest unknown).
- **Large uploads go straight from the browser to the object store** with a presigned PUT, through a custom indah component. This replaces the 200 MB in-memory path of ADR-0013 for datasets and models (the old path stays for small rasters). Needs CORS on SeaweedFS, which is unverified; slice S0 is that spike. If the store cannot do CORS, the fallback is the frontend streaming the body to the presigned URL without buffering it.
- **Attribution is part of the view:** a dataset's licence and attribution text is shown wherever its images are shown (the built-in SpaceNet dataset is CC BY-SA 4.0).
- **Local-only for now.** Everything is tenant-scoped, but public exposure, rate limits and abuse protection for uploads and GPU time are a separate hosting milestone.

## Result of slice S0 (2026-11-15): CORS plus presigned PUT works

Measured with `scripts/browser_upload_check.py` (Chromium, a file chosen through a file input, PUT to a presigned URL from a page on the frontend's origin; size and MD5 checked against the object):

| file | time | rate | frontend container memory |
|---|---|---|---|
| 300 MB | 3.2 s | 95 MB/s | 39 to 39 MiB |
| 2,000 MB | 22.9 s | 87 MB/s | 39 to 39 MiB |
| 4,900 MB | 59.0 s | 83 MB/s | 39 to 39 MiB |

- A single PUT works up to the 5 GB dataset limit; no multipart upload is needed for it.
- **SeaweedFS answers a CORS preflight for any origin when the bucket has no CORS rule** (it echoes the origin). A bucket rule restricts it: other origins get 403. The API now sets a rule on the upload bucket from `GEOPIPE_BROWSER_ORIGINS` (default `http://localhost:8080,http://127.0.0.1:8080`) and a stack test checks both the allowed and the refused origin. Production must set this variable to the real frontend origin.
- The streaming fallback is not needed.
- A test page that built the body from in-memory Blob parts failed above about 1 GB; a real file does not. Do not test uploads with Blobs.

## Alternatives considered

| Option | Why not |
|---|---|
| A separate `studio` service | A second frontend to run and secure, and the Detect tab would need to be reached from it; tabs share one session and one API client |
| Labelling in the studio | A project of its own; doubles the scope |
| Upload through the frontend server only | Datasets are larger than the 200 MB cap (SpaceNet's 300 tiles are 730 MB); buffering them in the server does not scale |

## Consequences

- The API grows a router per resource (datasets, models, runs). The frontend stays free of pixel and training code.
- `frontend/app.py` will outgrow one file; split per tab inside `stages/frontend/` when S2 lands.
- A custom indah component is code we maintain until indah offers presigned uploads.
