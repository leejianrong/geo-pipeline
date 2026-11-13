import hashlib
import time

import redis

from .bus import publish, set_status
from .messages import JOBS_IN, Job, Options


def derive_job_id(tenant_id: str, bucket: str, key: str, etag: str, options: Options) -> str:
    raw = f"{tenant_id}|{bucket}|{key}|{etag}|{options.model_dump_json()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def submit_job(r: redis.Redis, s3, bucket: str, key: str, tenant_id: str = "local",
               options: Options | None = None) -> str:
    """The single way to create a job (ADR-0008). Raises if the object does not exist."""
    options = options or Options()
    etag = s3.head_object(Bucket=bucket, Key=key)["ETag"]
    job_id = derive_job_id(tenant_id, bucket, key, etag, options)
    set_status(r, job_id, "queued", submitted_at=time.time())
    publish(r, JOBS_IN, Job(tenant_id=tenant_id, job_id=job_id, bucket=bucket, key=key,
                            options=options))
    return job_id
