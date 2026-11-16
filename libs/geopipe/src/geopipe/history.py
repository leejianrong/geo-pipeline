"""Durable job history in Postgres (GEO-41).

Valkey keeps live job status for a week and loses it on a restart. When `GEOPIPE_DATABASE_URL`
is set, every job that reaches a terminal state is also written here (`bus.set_status` calls
`record`), and the API reads this table for jobs Valkey no longer has. Writes never fail a job:
the caller logs and moves on. With no URL set nothing changes.
"""

import json
import logging
import os
import threading
import time
from urllib.parse import unquote, urlparse

import pg8000.exceptions
import pg8000.native

log = logging.getLogger("geopipe")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    kind text NOT NULL,
    state text NOT NULL,
    finished_at double precision NOT NULL,
    fields jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_tenant_finished ON jobs (tenant_id, finished_at DESC)
"""


class History:
    """One shared connection behind a lock; it reconnects once if the server went away."""

    def __init__(self, url: str):
        u = urlparse(url)
        self._args = dict(user=unquote(u.username or ""), password=unquote(u.password or ""),
                          host=u.hostname or "localhost", port=u.port or 5432,
                          database=(u.path or "/")[1:] or "postgres")
        self._lock = threading.Lock()
        self._conn = None
        for statement in SCHEMA.split(";"):
            self._run(statement)

    def _run(self, sql: str, **params):
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None:
                        self._conn = pg8000.native.Connection(**self._args)
                    return self._conn.run(sql, **params)
                except (pg8000.exceptions.InterfaceError, OSError):
                    self._conn = None  # the server restarted or dropped us: connect afresh
                    if attempt == 2:
                        raise

    def record(self, job_id: str, status: dict) -> None:
        """Upsert a finished job's whole status hash; ignored when it has no tenant."""
        tenant = status.get("tenant_id")
        if not tenant:
            return
        self._run(
            "INSERT INTO jobs (job_id, tenant_id, kind, state, finished_at, fields) "
            "VALUES (:id, :tenant, :kind, :state, :at, CAST(:fields AS jsonb)) "
            "ON CONFLICT (job_id) DO UPDATE SET kind = :kind, state = :state, "
            "finished_at = :at, fields = CAST(:fields AS jsonb)",
            id=job_id, tenant=tenant, kind=status.get("kind", "detect"),
            state=status.get("state", ""), at=time.time(), fields=json.dumps(status))

    def get(self, job_id: str, tenant_id: str) -> dict | None:
        rows = self._run("SELECT fields FROM jobs WHERE job_id = :id AND tenant_id = :tenant",
                         id=job_id, tenant=tenant_id)
        return rows[0][0] if rows else None

    def list(self, tenant_id: str, limit: int) -> list[dict]:
        """The tenant's most recently finished jobs, each with its `job_id`."""
        rows = self._run("SELECT job_id, fields FROM jobs WHERE tenant_id = :tenant "
                         "ORDER BY finished_at DESC LIMIT :limit", tenant=tenant_id, limit=limit)
        return [{"job_id": job_id, **fields} for job_id, fields in rows]


def from_env() -> History | None:
    url = os.environ.get("GEOPIPE_DATABASE_URL")
    return History(url) if url else None
