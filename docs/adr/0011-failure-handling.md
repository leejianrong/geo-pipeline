# ADR-0011: Retry, dead-letter and stall handling

- Status: Accepted
- Date: 2026-11-14
- Deciders: Jian, Claude

## Context

ADR-0001 gave at-least-once delivery, which only helps if failed and abandoned messages are actually retried. Until V4 a handler that raised left its message pending forever, a corrupt raster left its job unfinished, and a lost tile hung a job. Testing a 1,681-tile job showed it: postprocess failed on cleanup and the job sat in `finalising` until the test gave up after 15 minutes.

## Decision

- **Retry with backoff.** A handler that raises leaves its message pending. Any consumer of the group reclaims it once it has been idle for `GEOPIPE_RETRY_BASE_S × 2^(deliveries-1)` (capped at 8×; default base 15 s, compose sets 2 s for dev). After `GEOPIPE_MAX_ATTEMPTS` (3) deliveries it is dead-lettered.
- **Heartbeat.** While a handler runs, a thread resets the message's idle time every `base/3` seconds (`XCLAIM ... JUSTID`, which does not count as a delivery). Only messages of crashed or failed workers go idle, so a slow handler (a slicer on a big raster) is never duplicated.
- **Dead letters.** `jobs:dead` gets the original data, source stream, entry id, attempt count and the last error text. The job is failed. Unparseable messages are dead-lettered at once.
- **Permanent errors.** `PermanentError` (corrupt or CRS-less raster, no valid data, unknown model) fails the job immediately with an error event; retrying cannot help. A GDAL "does not exist" or "not recognized as ..." is permanent; other GDAL errors stay retryable because they may be network blips.
- **`fail_job`** sets the job `error`, records the reason, and publishes `jobs:done` with status `error`, once: it does nothing if the job already reached a terminal state (`done`, `incomplete`, `error`).
- **Idempotent finalise.** `finalise` returns early for terminal jobs and takes a `SET NX` lock (5-minute TTL, released on failure). The output key is deterministic. Late duplicate detections for a finished job are ignored.
- **Idempotent submit.** Job ids derive from tenant, object, ETag and options. Submitting again while a job is queued, running or done is a no-op; after `error` or `incomplete` it resets and retries.
- **Stall timeout.** Jobs are in a sorted set `jobs:deadlines`. The slicer (per tile) and postprocess (per detection) push the deadline out by the job's `options.timeout_seconds` (default 300). A sweeper in postprocess finalises jobs past their deadline: the GeoJSON gets `incomplete: true` and the exact `missing_tiles`, the job status is `incomplete`, and `jobs:done` says so. A job that never produced tiles is failed instead.

## Alternatives considered

| Option | Why not |
|--------|---------|
| A long idle threshold instead of heartbeats | Either duplicates slow handlers or makes crash recovery take minutes |
| Retry inside the handler | Does not help when the process itself dies |
| One fixed job deadline from submit time | A big raster legitimately takes long; progress-based is fairer |
| A separate scheduler service | More moving parts than a sorted set and a loop in an existing worker |

## Consequences

- A crashed detector's tiles are processed after the restart without losing or duplicating work. Verified by hand: with the detector killed mid-job and restarted, a 100-tile job finished with 650 features, identical to an uninterrupted run, with no duplicates. An automated test covers the bus-level behaviour (a dead consumer's message reclaimed, a slow handler not duplicated); killing a container is not automated because the test runner has no Docker socket.
- `incomplete` is terminal: tiles that arrive after the sweeper finalised a job are ignored, so rerunning needs a resubmit.
- The attempt limit is per message, not per job. A job with many tiles that each fail once still succeeds.
- If Valkey itself loses data, in-flight jobs are lost; persistence settings are outside this ADR.
- `fail_job` has a small race: two workers failing the same job at the same instant could both publish an error event. The status stays consistent.
