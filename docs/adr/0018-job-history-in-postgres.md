# ADR-0018: Finished jobs are also written to Postgres

- Status: Accepted
- Date: 2026-11-16
- Deciders: Jian, Claude

## Context

Job status lives in Valkey hashes that expire after a week and vanish if Valkey restarts without persistence. A hosted service needs users to see what they ran last month (GEO-41).

## Decision

- **Valkey stays the live truth**; stages keep writing status there, as before.
- **When a job reaches a terminal state** (`done`, `incomplete`, `error`, `cancelled`), `bus.set_status` also upserts its whole status hash into one Postgres table, `jobs` (`job_id`, `tenant_id`, `kind`, `state`, `finished_at`, `fields jsonb`). This one hook covers every stage and every kind of job (detect, ingest, train, bench), including the sweeper's failures.
- **A write never fails a job.** An exception is logged and the job goes on; a lost row is a gap in history, not a retry.
- **The API reads the table only for what Valkey no longer has:** `GET /jobs/{id}` falls back to it (same 404 for another tenant's job), and `GET /jobs` appends older rows after the live ones.
- **Opt in with `GEOPIPE_DATABASE_URL`.** Unset, nothing changes and no connection is made. Compose sets it and runs `postgres:17` with a named volume.
- **Driver: pg8000** (BSD-3, pure Python, no libpq), a core dependency of `geopipe`.
- Jobs still running when Valkey is lost are not recovered: their stream messages are gone as well. That is a durability question for the streams, not for the record of finished work.

## Alternatives considered

| Option | Why not |
|---|---|
| The API writes the row at submit and `jobs:done` updates it | Ingest, training and bench jobs never publish `jobs:done`, and a second writer would need its own retry story |
| Valkey persistence (AOF) only | Fixes restarts, not the one-week expiry, and gives no query by tenant over months |
| psycopg | LGPL and needs libpq; pg8000 is enough for one small table |
| Write every progress update | Hundreds of writes per job for no reader |

## Consequences

- One more service in compose and a migration path to think about later (the schema is created with `IF NOT EXISTS` for now).
- The record holds the same fields as the status hash, so anything added to a status appears in history for free.
