# ADR-0008: Build the MVP as stateless workers with a service-shaped contract

- Status: Accepted
- Date: 2026-11-13
- Deciders: Jian, Claude

## Context

The MVP is a local compose stack, but the owner wants a hosted service with a frontend later. Retrofitting identity, status reporting and storage layout is expensive; adding an API in front of a well-shaped message contract is cheap.

## Decision

- Workers are stateless. State lives only in Valkey (streams, aggregation, status) and the object store.
- All job creation goes through `submit_job()` in `libs/geopipe`. CLI now, API later; no second write path.
- Every message and S3 key carries `tenant_id` (default `local`): `tenants/<tid>/…`. Not enforced in MVP.
- Every stage writes `job:<id>:status` with state and progress counts; this is the future status endpoint.
- Object storage is used only through the S3 API with a configurable endpoint. Uploads and downloads to users will use presigned URLs, so file bytes never pass through the API.
- Config via environment; each stage exposes a health check; logs are structured and carry `job_id` and `tenant_id`.
- Post-MVP additions: `api` (FastAPI), `frontend`, `postgres` (durable job records), auth. None require changes to worker contracts.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Build the API and frontend now | Scope growth before the pipeline is proven |
| Ignore hosting, add later | Message and key schemas would need a breaking migration |
| Postgres job store now | Extra service with no MVP consumer; Valkey hash is enough |

## Consequences

Small extra cost now (a field and a key prefix). Tenant isolation is a convention until V6 enforces it, so MVP data should be treated as single-tenant. Valkey status is volatile; durable history waits for Postgres.
