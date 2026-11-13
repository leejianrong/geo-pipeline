# ADR-0002: Slicer writes tiles as PNG to S3 scratch; messages carry references

- Status: Accepted
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

Stage 1 slices; stage 2 detects. Tile pixels have to cross a process boundary. A 640×640×3 tile is about 1.2 MB raw.

## Decision

The slicer writes 8-bit RGB PNG tiles to `s3://<scratch>/<job_id>/<idx>.png` and publishes a small message with the URI and window. Postprocess deletes a job's tiles after finalising, and a periodic sweep removes orphans from failed jobs. (Bucket lifecycle rules are not relied on; SeaweedFS support is unchecked.) Tiles are never put in Redis.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Pixels inside Redis messages | Memory pressure on Redis; large stream entries |
| Detector reads the TIFF window itself | Needs GDAL in the detector image, contradicting ADR-0003; reads the source twice |
| Raw `.npy` | Larger than PNG, and no benefit once tiles are 8-bit |

## Consequences

Detector is GDAL-free and horizontally scalable; tiles are inspectable and replayable (a failed job can be re-detected without re-slicing). Costs an extra S3 write and read per tile and requires scratch cleanup, which we own. PNG is lossless, so there is no accuracy impact.
