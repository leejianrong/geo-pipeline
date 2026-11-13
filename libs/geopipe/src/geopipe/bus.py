"""Valkey Streams helpers: publish, consume with retries and dead-lettering, job status.

Delivery is at-least-once. A handler that raises leaves its message pending; it is
reclaimed with exponential backoff and dead-lettered after `max_attempts` deliveries.
While a handler runs, a heartbeat keeps its message from looking abandoned, so only
messages of crashed or failed workers are reclaimed.
"""

import logging
import os
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager

import redis
from pydantic import BaseModel, ValidationError

from .messages import DEADLINES, JOBS_DEAD, JOBS_DONE, Done

log = logging.getLogger("geopipe")
STATUS_TTL = 7 * 24 * 3600
TERMINAL = ("done", "incomplete", "error")
RETRY_BASE_S = float(os.environ.get("GEOPIPE_RETRY_BASE_S", "15"))
MAX_ATTEMPTS = int(os.environ.get("GEOPIPE_MAX_ATTEMPTS", "3"))
BLOCK_MS = 1000  # must stay well below the socket timeout in connect()


class PermanentError(Exception):
    """Retrying cannot help (bad input, unknown model): fail the job now."""


def backoff(deliveries: int, base: float) -> float:
    """Seconds a message must sit idle after its n-th delivery before it is reclaimed."""
    return min(base * 2 ** (deliveries - 1), base * 8)


def connect() -> redis.Redis:
    # socket_timeout must exceed BLOCK_MS
    return redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://localhost:6379/0"),
                                decode_responses=True, socket_timeout=30)


def publish(r: redis.Redis, stream: str, msg: BaseModel) -> None:
    r.xadd(stream, {"data": msg.model_dump_json()}, maxlen=100_000, approximate=True)


def set_status(r: redis.Redis, job_id: str, state: str, **fields) -> None:
    key = f"job:{job_id}:status"
    r.hset(key, mapping={"state": state, **{k: str(v) for k, v in fields.items()}})
    r.expire(key, STATUS_TTL)


def touch(r: redis.Redis, job_id: str) -> None:
    """Record progress: push the job's stall deadline out by its timeout."""
    timeout = float(r.hget(f"job:{job_id}:status", "timeout_seconds") or 300)
    r.zadd(DEADLINES, {job_id: time.time() + timeout})


def fail_job(r: redis.Redis, tenant_id: str, job_id: str, reason: str) -> bool:
    """Mark a job as failed and announce it, unless it already reached a terminal state."""
    if r.hget(f"job:{job_id}:status", "state") in TERMINAL:
        return False
    set_status(r, job_id, "error", error=reason)
    r.zrem(DEADLINES, job_id)
    publish(r, JOBS_DONE, Done(tenant_id=tenant_id, job_id=job_id, status="error", error=reason))
    log.error("job %s failed: %s", job_id, reason)
    return True


def _dead_letter(r, stream, group, entry_id, fields, deliveries, reason) -> None:
    r.xadd(JOBS_DEAD, {"data": fields.get("data", ""), "stream": stream, "id": entry_id,
                       "attempts": deliveries, "reason": reason[:500]},
           maxlen=10_000, approximate=True)
    r.xack(stream, group, entry_id)
    log.error("dead-lettered %s from %s after %s deliveries: %s", entry_id, stream, deliveries,
              reason)


@contextmanager
def _heartbeat(r, stream, group, consumer, entry_id, interval):
    """Reset the message's idle time (without counting a delivery) while its handler runs."""
    stop = threading.Event()

    def beat():
        while not stop.wait(interval):
            try:
                r.xclaim(stream, group, consumer, 0, [entry_id], justid=True)
            except redis.RedisError:
                log.exception("heartbeat failed for %s", entry_id)

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=interval + 1)


def _reclaim(r, stream, group, consumer, retry_base) -> list[tuple[str, dict, int]]:
    """Take over pending messages whose backoff has elapsed: (id, fields, delivery count)."""
    out = []
    for p in r.xpending_range(stream, group, "-", "+", count=20):
        need_ms = int(backoff(p["times_delivered"], retry_base) * 1000)
        if p["time_since_delivered"] < need_ms:
            continue
        for entry_id, fields in r.xclaim(stream, group, consumer, need_ms, [p["message_id"]]):
            if fields:
                out.append((entry_id, fields, p["times_delivered"] + 1))
    return out


def _process(r, stream, group, consumer, model, handler, entry, retry_base, max_attempts):
    entry_id, fields, deliveries = entry
    try:
        msg = model.model_validate_json(fields["data"])
    except (ValidationError, KeyError) as e:
        _dead_letter(r, stream, group, entry_id, fields, deliveries, f"unparseable: {e}")
        return
    if deliveries > max_attempts:
        reason = r.get(f"fail:{stream}:{entry_id}") or "unknown error"
        _dead_letter(r, stream, group, entry_id, fields, deliveries, reason)
        fail_job(r, msg.tenant_id, msg.job_id,
                 f"gave up after {max_attempts} attempts on {stream}: {reason}")
        return
    with _heartbeat(r, stream, group, consumer, entry_id, retry_base / 3):
        try:
            handler(msg)
        except PermanentError as e:
            fail_job(r, msg.tenant_id, msg.job_id, str(e))
            r.xack(stream, group, entry_id)
        except Exception as e:
            log.exception("handler failed for %s on %s (delivery %s)", entry_id, stream,
                          deliveries)
            r.set(f"fail:{stream}:{entry_id}", f"{type(e).__name__}: {e}"[:500], ex=86400)
        else:
            r.xack(stream, group, entry_id)


def consume[M: BaseModel](r: redis.Redis, stream: str, group: str, consumer: str,
                          model: type[M], handler: Callable[[M], None], *,
                          retry_base: float = RETRY_BASE_S, max_attempts: int = MAX_ATTEMPTS,
                          stop: threading.Event | None = None) -> None:
    """Run until `stop` is set (forever if None). Acks only after the handler succeeds."""
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    last_reclaim = 0.0
    while not (stop and stop.is_set()):
        entries: list[tuple[str, dict, int]] = []
        if time.time() - last_reclaim >= retry_base / 2:
            last_reclaim = time.time()
            entries += _reclaim(r, stream, group, consumer, retry_base)
        for _, new in r.xreadgroup(group, consumer, {stream: ">"}, count=1, block=BLOCK_MS) or []:
            entries += [(entry_id, fields, 1) for entry_id, fields in new]
        for entry in entries:
            _process(r, stream, group, consumer, model, handler, entry, retry_base, max_attempts)
