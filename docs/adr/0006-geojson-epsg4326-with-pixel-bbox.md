# ADR-0006: Emit GeoJSON in EPSG:4326, keeping the pixel box and source CRS as properties

- Status: Accepted
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

RFC 7946 GeoJSON is WGS84 lon/lat only. Source rasters are often in a projected CRS. Precision matters for the 1-pixel georeferencing target.

## Decision

Convert box corners from raster pixels to the source CRS with the geotransform, then reproject to EPSG:4326. Each feature is a Polygon (the box corners) with `pixel_bbox`, `class_name`, `class_id`, `score`, `job_id`. The collection carries `source_crs` (WKT), `schema_version`, `model`, `incomplete` and `missing_tiles`.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Output in the raster CRS with a legacy `crs` member | Many GeoJSON consumers ignore or reject it |
| Centre points only | Loses extent; boxes are what the model produces |

## Consequences

Standards-compliant and QGIS-friendly. An axis-aligned pixel box becomes a slightly rotated quadrilateral after reprojection, which is correct. Users wanting metric output need an option (not in MVP).
