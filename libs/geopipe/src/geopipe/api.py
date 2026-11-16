"""HTTP API (V6): a thin front door over `submit_job`, job status and presigned URLs.

File bytes never pass through the API: uploads and results use presigned S3 URLs. The tenant
comes from the API key and scopes every S3 key (`tenants/<tid>/...`) and every job lookup.
"""

import os
import secrets
import uuid
from dataclasses import dataclass

import redis
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import identity, store
from .api_studio import add_dataset_routes, add_model_routes, add_train_routes
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


@dataclass(frozen=True)
class Quotas:
    """What one tenant may store (ADR-0017): file sizes, checked when an upload URL is issued,
    and how many datasets and models it may hold at once."""

    dataset_bytes: int = 5 * 1024**3
    model_bytes: int = 500 * 1024**2
    datasets: int = 20
    models: int = 20

    @classmethod
    def from_env(cls) -> "Quotas":
        def number(name: str, default: int) -> int:
            return int(os.environ.get(name, default))

        return cls(number("GEOPIPE_MAX_DATASET_BYTES", cls.dataset_bytes),
                   number("GEOPIPE_MAX_MODEL_BYTES", cls.model_bytes),
                   number("GEOPIPE_MAX_DATASETS", cls.datasets),
                   number("GEOPIPE_MAX_MODELS", cls.models))


class UploadRequest(BaseModel):
    size: int = Field(ge=1, description="the file's size in bytes; the URL accepts only that")


DEFAULT_QUOTAS = Quotas()


def create_app(r: redis.Redis, s3, public_s3, bucket: str, keys: dict[str, str],
               quotas: Quotas = DEFAULT_QUOTAS, history=None,
               session_secret: str | None = None) -> FastAPI:
    """`public_s3` signs URLs with the endpoint clients can reach (it may differ from `s3`).
    `history` (Postgres) answers for jobs Valkey no longer has. With `session_secret`,
    `Authorization: Bearer <token>` signed by `geopipe.identity` (the web UI sends one after a
    GitHub or Google sign-in) names the tenant, beside the API keys."""
    app = FastAPI(title="geo-pipeline", version="0.1.0")

    def tenant(x_api_key: str = Header(default=""), authorization: str = Header(default="")) -> str:
        if session_secret and authorization.startswith("Bearer "):
            claims = identity.verify(session_secret, authorization.removeprefix("Bearer "))
            if claims and isinstance(claims.get("tenant"), str) and identity.TENANT.fullmatch(
                    claims["tenant"]) and claims["tenant"] != SHARED:
                return claims["tenant"]
        for k, t in keys.items():  # constant-time compare, no early exit on a prefix match
            if secrets.compare_digest(k, x_api_key):
                return t
        raise HTTPException(401, "missing or invalid X-API-Key")

    def own_status(job_id: str, tid: str) -> dict:
        status = r.hgetall(f"job:{job_id}:status")
        if not status and history:
            status = history.get(job_id, tid) or {}
        if not status or status.get("tenant_id") != tid:
            raise HTTPException(404, "unknown job")  # same answer for another tenant's job
        return status

    add_dataset_routes(app, r, s3, public_s3, bucket, tenant)
    add_model_routes(app, r, s3, bucket, tenant)
    add_train_routes(app, r, s3, bucket, tenant)

    @app.get("/health")
    def health() -> dict:
        r.ping()
        return {"ok": True}

    @app.post("/uploads")
    def create_upload(tid: str = Depends(tenant)) -> dict:
        return presigned_put(tid, f"{uuid.uuid4().hex}.tif")

    def check_upload(tid: str, size: int, kind: str) -> None:
        """Refuse an upload that is too big, or that would be one dataset or model too many."""
        limit = quotas.dataset_bytes if kind == "dataset" else quotas.model_bytes
        held = r.zcard(f"tenant:{tid}:{kind}s")
        cap = quotas.datasets if kind == "dataset" else quotas.models
        if size > limit:
            raise HTTPException(
                413, f"a {kind} upload may be at most {limit / 1024**2:.0f} MB; this is "
                     f"{size / 1024**2:.0f} MB")
        if held >= cap:
            raise HTTPException(
                409, f"you already have {held} {kind}s, the limit; delete one to add another")

    @app.post("/uploads/model")
    def create_model_upload(req: UploadRequest, tid: str = Depends(tenant)) -> dict:
        """A presigned PUT for an ONNX model. The URL is signed for exactly `size` bytes, so the
        store refuses any other length; the browser sends the file straight to the store."""
        check_upload(tid, req.size, "model")
        return presigned_put(tid, f"models/{uuid.uuid4().hex}.onnx", req.size)

    @app.post("/uploads/dataset")
    def create_dataset_upload(req: UploadRequest, tid: str = Depends(tenant)) -> dict:
        """A presigned PUT for a dataset zip, signed for exactly `size` bytes."""
        check_upload(tid, req.size, "dataset")
        return presigned_put(tid, f"datasets/{uuid.uuid4().hex}.zip", req.size)

    def presigned_put(tid: str, name: str, size: int | None = None) -> dict:
        key = f"tenants/{tid}/uploads/{name}"
        params = {"Bucket": bucket, "Key": key} | ({"ContentLength": size} if size else {})
        url = public_s3.generate_presigned_url("put_object", Params=params, ExpiresIn=3600)
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
        limit = max(1, min(limit, 200))
        ids = r.zrevrange(f"tenant:{tid}:jobs", 0, limit - 1)
        jobs = [{"job_id": i, **s} for i in ids if (s := r.hgetall(f"job:{i}:status"))]
        if history:  # older jobs, from the durable record
            seen = set(ids)
            jobs += [j for j in history.list(tid, limit) if j["job_id"] not in seen]
        return jobs[:limit]

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
    r = bus.connect()
    return create_app(r, s3, public, bucket, parse_keys(os.environ["GEOPIPE_API_KEYS"]),
                      Quotas.from_env(), bus.history_sink(),
                      os.environ.get("GEOPIPE_SESSION_SECRET") or None)
