# Questions

## Open forks

None.

## Register

| ID | Question | Status | Answer or default | Landed |
|----|----------|--------|-------------------|--------|
| Q1 | Trigger mechanism | DECIDED | Redis-protocol message naming an S3 object | ADR-0001 |
| Q2 | S3 store | DECIDED → revised | Must be non-AGPL, so SeaweedFS, not Garage | ADR-0009 |
| Q3 | Models | DECIDED | RF-DETR (default) and YOLOX; no AGPL | ADR-0004 |
| Q4 | Final output | DECIDED | GeoJSON file | ADR-0006 |
| Q5 | Queue semantics | DECIDED | Streams with consumer groups | ADR-0001 |
| Q6 | Old notes and history | DECIDED | Deleted; fresh git repo | n/a |
| Q7 | Base image | DECIDED | Own GDAL images for Python 3.12 and 3.13 with numpy 2.x | ADR-0007 |
| Q8 | Raster size, bands, dtype | ASSUMED | GeoTIFF 0.5–5 GB, 1–8 bands, uint8/uint16 | PLAN |
| Q9 | Tile size and overlap | ASSUMED | 640 px, 20% | PLAN |
| Q10 | Output CRS | ASSUMED | EPSG:4326 | ADR-0006 |
| Q11 | CPU or GPU | ASSUMED | CPU in MVP, GPU in V7 | SLICES V7 |
| Q12 | Queue server | DECIDED (licence rule) | Valkey | ADR-0001 |
| Q13 | Box type | ASSUMED | Axis-aligned | SLICES V5 |
| Q14 | Tile transport | ASSUMED | PNG in S3 scratch, refs in messages | ADR-0002 |
| Q15 | Image split | ASSUMED | GDAL images vs GDAL-free detector image | ADR-0003 |
| Q16 | Aggregation state | ASSUMED | Valkey, finalise by count or timeout | ADR-0005 |
| Q17 | Success criteria | ASSUMED | ≤1 px georef error; seam objects once; bounded RSS; benchmark metrics | SLICES |
| Q18 | Secrets | ASSUMED | Env only, never logged; `.env` gitignored | PLAN |
| Q19 | Versioning | ASSUMED | `schema_version` on messages and GeoJSON; no migrations yet | PLAN |
| Q20 | Benchmark dataset | DECIDED (generic, `.tif`, scorable) | SpaceNet | ADR-0010 |
| Q21 | Aerial weights | ASSUMED | One-off fine-tune on SpaceNet in V5 | SLICES V5 |
| Q22 | Base OS | ASSUMED | Debian trixie (GDAL 3.10, tested on 3.12 and 3.13) | ADR-0007 |
| Q23 | Job status store | ASSUMED | Valkey hash in MVP, Postgres when hosted | ADR-0008 |
| Q24 | SeaweedFS and GDAL compatibility | ASSUMED | Works; V1 tests it | ADR-0009 |
| Q25 | Hosted-service shape | DECIDED (direction) | Stateless workers, tenant-aware keys, single submit path, API/frontend later | ADR-0008 |
| Q26 | Python versions | DECIDED | 3.12 and 3.13 both supported for GDAL images; detector defaults to 3.12 | ADR-0003, ADR-0007 |
| Q27 | Licence policy | DECIDED | No AGPL anywhere; CI check | ADR-0009 |

## Coverage

| Category | Covered by |
|----------|-----------|
| Primary user and actors | PLAN §Users |
| Scope boundary | PLAN §Scope, Q6 |
| Data model and identity | Q14, PLAN §Implementation (tenant_id, job_id, idx, window) |
| State and storage | Q14, Q16, Q23 |
| Concurrency and conflict | Q5, Q16, SLICES V4 |
| Interfaces and contracts | Q1, Q25, PLAN §Implementation |
| Failure behaviour | SLICES V4 |
| External dependencies | Q2, Q3, Q7, Q12, Q20, Q27 |
| Runtime and deployment | Q15, Q22, PLAN §Architecture |
| Measurable success | Q17, SLICES V5 |
| Security and secrets | Q18, ADR-0008 (tenant isolation, later) |
| Versioning and migration | Q19 |
