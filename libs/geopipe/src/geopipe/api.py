"""HTTP API (V6): a thin front door over `submit_job`, job status and presigned URLs.

File bytes never pass through the API: uploads and results use presigned S3 URLs. The tenant
comes from the API key and scopes every S3 key (`tenants/<tid>/...`) and every job lookup.
"""

import os
import secrets
import uuid

import redis
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import store
from .api_studio import add_dataset_routes
from .jobs import submit_job
from .messages import Options
from .registry import SHARED


class JobRequest(BaseModel):
    key: str  # a `key` returned by POST /uploads
    options: Options = Options()


def parse_keys(spec: str) -> dict[str, str]:
    """`key1:tenant1,key2:tenant2` -> {key: tenant}."""
    pairs = [p.split(":", 1) for p in spec.split(",") if p.strip()]
    if any(len(p) != 2 or not all(p) for p in pairs):
        raise ValueError("GEOPIPE_API_KEYS must look like key1:tenant1,key2:tenant2")
    if any(t.strip() == SHARED for _, t in pairs):
        raise ValueError(f"{SHARED!r} is the built-in namespace, not a tenant name")
    return {k.strip(): t.strip() for k, t in pairs}


def create_app(r: redis.Redis, s3, public_s3, bucket: str, keys: dict[str, str]) -> FastAPI:
    """`public_s3` signs URLs with the endpoint clients can reach (it may differ from `s3`)."""
    app = FastAPI(title="geo-pipeline", version="0.1.0")

    def tenant(x_api_key: str = Header(default="")) -> str:
        for k, t in keys.items():  # constant-time compare, no early exit on a prefix match
            if secrets.compare_digest(k, x_api_key):
                return t
        raise HTTPException(401, "missing or invalid X-API-Key")

    def own_status(job_id: str, tid: str) -> dict:
        status = r.hgetall(f"job:{job_id}:status")
        if not status or status.get("tenant_id") != tid:
            raise HTTPException(404, "unknown job")  # same answer for another tenant's job
        return status

    add_dataset_routes(app, r, s3, public_s3, bucket, tenant)

    @app.get("/health")
    def health() -> dict:
        r.ping()
        return {"ok": True}

    @app.post("/uploads")
    def create_upload(tid: str = Depends(tenant)) -> dict:
        key = f"tenants/{tid}/uploads/{uuid.uuid4().hex}.tif"
        url = public_s3.generate_presigned_url(
            "put_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600)
        return {"key": key, "upload_url": url, "method": "PUT"}

    @app.post("/jobs", status_code=202)
    def create_job(req: JobRequest, tid: str = Depends(tenant)) -> dict:
        if ".." in req.key or not req.key.startswith(f"tenants/{tid}/uploads/"):
            raise HTTPException(403, "key is not one of your uploads")
        try:
            return {"job_id": submit_job(r, s3, bucket, req.key, tid, req.options)}
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                raise HTTPException(404, "upload not found: PUT the file first") from e
            raise

    @app.get("/jobs")
    def list_jobs(limit: int = 50, tid: str = Depends(tenant)) -> list[dict]:
        """The tenant's newest jobs (statuses expire after a few days, so older ones drop out)."""
        ids = r.zrevrange(f"tenant:{tid}:jobs", 0, max(1, min(limit, 200)) - 1)
        return [{"job_id": i, **s} for i in ids if (s := r.hgetall(f"job:{i}:status"))]

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str, tid: str = Depends(tenant)) -> dict:
        return own_status(job_id, tid)

    @app.get("/jobs/{job_id}/result")
    def get_result(job_id: str, tid: str = Depends(tenant)) -> dict:
        status = own_status(job_id, tid)
        if status["state"] not in ("done", "incomplete"):
            raise HTTPException(409, f"job is {status['state']}, no result yet")

        def url(uri: str) -> str:
            bucket_, key_ = uri.removeprefix("s3://").split("/", 1)
            return store.presign_get(public_s3, bucket_, key_)

        return {"state": status["state"], "url": url(status["output_uri"]),
                "preview_url": url(status["preview_uri"]) if "preview_uri" in status else None,
                "width": int(status["width"]) if "width" in status else None,
                "height": int(status["height"]) if "height" in status else None}

    return app


def app_from_env() -> FastAPI:  # `uvicorn geopipe.api:app_from_env --factory`
    from . import bus

    s3 = store.client()
    bucket = os.environ.get("GEOPIPE_BUCKET", "imagery")
    store.ensure_bucket(s3, bucket)
    origins = os.environ.get("GEOPIPE_BROWSER_ORIGINS", "http://localhost:8080,http://127.0.0.1:8080")
    store.allow_browser_uploads(s3, bucket, origins.split(","))
    public = store.client(os.environ.get("S3_PUBLIC_ENDPOINT"))
    return create_app(bus.connect(), s3, public, bucket,
                      parse_keys(os.environ["GEOPIPE_API_KEYS"]))
