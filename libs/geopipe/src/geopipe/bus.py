"""Valkey Streams helpers: publish, consume with ack-after-success, job status."""

import logging
import os
from collections.abc import Callable

import redis
from pydantic import BaseModel

log = logging.getLogger("geopipe")
STATUS_TTL = 7 * 24 * 3600


def connect() -> redis.Redis:
    # socket_timeout must exceed the XREADGROUP block time used in consume()
    return redis.Redis.from_url(os.environ.get("VALKEY_URL", "redis://localhost:6379/0"),
                                decode_responses=True, socket_timeout=30)


def publish(r: redis.Redis, stream: str, msg: BaseModel) -> None:
    r.xadd(stream, {"data": msg.model_dump_json()}, maxlen=100_000, approximate=True)


def set_status(r: redis.Redis, job_id: str, state: str, **fields) -> None:
    key = f"job:{job_id}:status"
    r.hset(key, mapping={"state": state, **{k: str(v) for k, v in fields.items()}})
    r.expire(key, STATUS_TTL)


def consume[M: BaseModel](r: redis.Redis, stream: str, group: str, consumer: str,
                          model: type[M], handler: Callable[[M], None]) -> None:
    """Run forever. A message is acked only after the handler returns; on error it stays
    pending (redelivery is added in V4)."""
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    while True:
        for _, entries in r.xreadgroup(group, consumer, {stream: ">"}, count=1, block=5000) or []:
            for entry_id, fields in entries:
                try:
                    handler(model.model_validate_json(fields["data"]))
                    r.xack(stream, group, entry_id)
                except Exception:
                    log.exception("handler failed for %s on %s", entry_id, stream)
