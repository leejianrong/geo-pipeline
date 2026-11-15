import hashlib
import time

import redis

from .bus import publish, set_status
from .messages import INGEST, JOBS_IN, IngestJob, Job, Options


def derive_job_id(tenant_id: str, bucket: str, key: str, etag: str, options: Options) -> str:
    raw = f"{tenant_id}|{bucket}|{key}|{etag}|{options.model_dump_json()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def submit_job(r: redis.Redis, s3, bucket: str, key: str, tenant_id: str = "local",
               options: Options | None = None) -> str:
    """The single way to create a job (ADR-0008). Raises if the object does not exist."""
    options = options or Options()
    etag = s3.head_object(Bucket=bucket, Key=key)["ETag"]
    job_id = derive_job_id(tenant_id, bucket, key, etag, options)
    status_key = f"job:{job_id}:status"
    if not r.hsetnx(status_key, "state", "queued"):
        if r.hget(status_key, "state") not in ("error", "incomplete"):
            return job_id  # already queued, running or done: submitting again is a no-op
        r.delete(status_key, f"job:{job_id}:dets", f"job:{job_id}:finalise", f"job:{job_id}:meta")
        r.hset(status_key, "state", "queued")  # retry of a failed job
    set_status(r, job_id, "queued", submitted_at=time.time(), tenant_id=tenant_id,
               timeout_seconds=options.timeout_seconds)
    r.zadd(f"tenant:{tenant_id}:jobs", {job_id: time.time()})  # the job list the API serves
    publish(r, JOBS_IN, Job(tenant_id=tenant_id, job_id=job_id, bucket=bucket, key=key,
                            options=options))
    return job_id


def submit_ingest(r: redis.Redis, bucket: str, tenant_id: str, upload_key: str, dataset_id: str,
                  name: str, licence: str, attribution: str) -> str:
    """Queue an uploaded dataset zip for validation. The upload is checked by the worker, not
    here, so a missing file is reported the same way as any other bad input."""
    job_id = hashlib.sha256(f"ingest|{tenant_id}|{dataset_id}".encode()).hexdigest()[:16]
    set_status(r, job_id, "queued", submitted_at=time.time(), tenant_id=tenant_id, kind="ingest",
               dataset_id=dataset_id)
    publish(r, INGEST, IngestJob(tenant_id=tenant_id, job_id=job_id, bucket=bucket,
                                 upload_key=upload_key, dataset_id=dataset_id, name=name,
                                 licence=licence, attribution=attribution))
    return job_id
