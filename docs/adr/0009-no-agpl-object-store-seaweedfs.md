# ADR-0009: No AGPL components; use SeaweedFS as the local S3 store

- Status: Accepted
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

The owner ruled out AGPL. The earlier plan named Garage, which is AGPL-3.0, and Ultralytics YOLO and MinIO are also AGPL. The project itself will be Apache-2.0. A hosted service makes network-use copyleft (AGPL) clauses a real concern.

One web search (blog sources, not primary) lists SeaweedFS and RustFS as Apache-2.0 S3-compatible stores and Garage as AGPL-3.0.

## Decision

- Repository licence: Apache-2.0.
- Object store: SeaweedFS (Apache-2.0, S3 gateway in a single container). Fallback: RustFS.
- Queue: Valkey (ADR-0001). Models: RF-DETR (Apache-2.0 sizes) and YOLOX (ADR-0004).
- Enforcement: CI runs a licence check over each image's installed Python packages and fails on AGPL or GPL (LGPL allowed).
- Carve-out: the Debian userland and shared libraries in the base image (bash, coreutils, GDAL's dependencies) include GPL programs run as separate processes; these are accepted. The AGPL ban applies to what we choose, link into our code, or run as a service. A short reviewed list of third-party containers (Valkey, SeaweedFS) is kept in the README with licences confirmed from each project's LICENSE file when added.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Garage | AGPL-3.0 |
| MinIO | AGPL; also reportedly in maintenance mode |
| moto server | In-memory test double, not a realistic store |
| Ceph RGW | LGPL, and heavyweight for a local stack |

## Consequences

Clean licence story for a hosted service. SeaweedFS and RustFS are less familiar than MinIO; GDAL `/vsis3/` range reads and presigned URLs against SeaweedFS must be proven in V1 (Q24). The licence check only covers Python packages. A grep of `/usr/share/doc/*/copyright` in `geo-gdal:py3.12` found an AGPL mention only in `ca-certificates` (not reviewed in detail, believed to be helper scripts, not linked); the OS layer needs a one-time manual review before release. Licences stated here for Valkey, SeaweedFS and YOLOX come from search results or memory, so confirm against each LICENSE file before first release.
