# ADR-0005: Postprocess aggregates in Redis and finalises by count or timeout

- Status: Accepted
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

Detections for one image arrive from several detector replicas, interleaved with other jobs, possibly duplicated by redelivery. An in-memory dict would lose everything when postprocess restarts.

## Decision

Postprocess writes each tile's boxes to a Redis hash `job:<id>:dets` (field = tile `idx`, so redelivery overwrites rather than duplicates). When `HLEN == total` it takes a `SET NX` finalise lock, merges, writes GeoJSON, publishes `jobs:done`, then deletes the keys. A per-job deadline finalises partial results with `incomplete: true` and the list of missing indices. Output keys are deterministic so a repeated finalise overwrites the same object.

## Alternatives considered

| Option | Why not |
|--------|---------|
| In-memory dict | State lost on restart; single postprocess instance only |
| Store boxes in S3 | Slower, more moving parts for tiny payloads |

## Consequences

Restart-safe, replicable, idempotent. Redis must be configured with persistence or the system accepts that a Redis loss drops in-flight jobs. Keys need a TTL as a leak guard.
