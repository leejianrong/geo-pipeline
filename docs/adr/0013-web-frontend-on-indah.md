# ADR-0013: Web frontend built on indah, as a client of the HTTP API

- Status: Accepted
- Date: 2026-11-14
- Deciders: Jian, Claude

## Context

V8 needs upload, live progress, a view of detections over the raster with filters, job history, a GeoJSON download and sign-in. The owner's own UI framework, indah (Apache-2.0, on PyPI), is a Python library with a pre-built shell and no Node toolchain. It already has `Upload`, `Download`, a rich `Table`, `Progress`, and an `ImageOverlay` that draws boxes with hover labels over an image, with zoom and pan.

## Decision

- The frontend is an indah app (`stages/frontend/app.py`, pinned `indah==0.13.0`), one more compose service on `localhost:8080`. No JavaScript build, no second language in the repo.
- It is a client of the V6 API only: no Valkey or S3 credentials. Upload bytes go browser to the frontend server, then to a presigned URL; previews and GeoJSON are fetched the same way. The browser never receives a presigned URL.
- Detections are drawn over a low-resolution JPEG preview of the raster that the slicer writes next to the result (`tenants/<tid>/results/<job>.preview.jpg`, from the overview it already reads). Boxes use the GeoJSON `pixel_bbox`, so the view is in raster pixels, not a web map: no basemap, no reprojection.
- "Sign in" is the tenant's API key typed into the page. The key is held in the viewer's server-side session. Real identity (an identity provider mapped to a tenant) is a later card.
- The API gained `GET /jobs` (a per-tenant Valkey sorted set written by `submit_job`), and `GET /jobs/{id}/result` also returns the preview URL and the raster size. Filtering and box placement live in `geopipe.viewer` and are unit-tested without indah.

## Alternatives considered

| Option | Why not |
|---|---|
| React or Svelte single-page app | A Node toolchain, a second language and a CORS and presigned-URL story for the browser, for a UI that is mostly forms, a table and one image |
| Static HTML with Leaflet | Needs reprojected rasters or a tile server to draw "over the raster"; the preview and pixel boxes are simpler and exact |
| indah's `Map` component | Takes lat/lon geometry over a web basemap; useful later, but it would not show the raster itself |
| Another Python UI framework | indah is the owner's, already has every component needed, and is a good test of it with a real service behind it |

## Consequences

- indah ships as a dependency of one container only; the workers and the API do not import it.
- **Upload size:** file bytes pass through the frontend server in memory, capped at `UI_MAX_UPLOAD_MB` (default 200). Larger rasters go through the API or CLI. A direct browser-to-store upload needs a custom indah component and CORS on the store; it is a follow-up, not part of this slice.
- The class filter is a text box ("class contains"), because indah's selection components take their options at build time and the class list differs per model.
- The raster preview is at most about 2048 px on its longest side, so tiny detections on a very large raster are small on screen. Zoom shows the preview's pixels, not the raster's.
- Verified through the indah session dispatch path (the same one a browser event takes) in `tests/stack/test_frontend.py`, plus a one-off real-browser run (Playwright, Chromium) that signed in, uploaded a GeoTIFF, and showed RF-DETR boxes over it. No browser test runs in CI.
