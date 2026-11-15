"""Ingest of an uploaded ONNX model (ADR-0017), run by the trainer stage: check, copy, register.

Like dataset ingest, bad input raises `bus.PermanentError` and leaves a `failed` manifest with the
report for the owner; the manifest of a good model is written last.
"""

import time

from botocore.exceptions import ClientError

from . import bus, registry
from .dataset_zip import ProblemsFound
from .messages import ModelIngestJob
from .onnx_check import MAX_BYTES, validate_onnx


def upload_prefix(tenant_id: str) -> str:
    return f"tenants/{tenant_id}/uploads/models/"


def ingest_model(r, s3, job: ModelIngestJob, now=time.time, max_bytes: int = MAX_BYTES) -> None:
    if not registry.ID_RE.match(job.model_id):
        raise bus.PermanentError(f"bad model id {job.model_id!r}")
    manifest = registry.ModelManifest(
        id=job.model_id, name=job.name, decoder=job.decoder, input_size=0, classes=job.classes,
        licence=job.licence, state="failed", created_at=now())
    try:
        bus.set_status(r, job.job_id, "validating", tenant_id=job.tenant_id, kind="model",
                       model_id=job.model_id)
        if ".." in job.upload_key or not job.upload_key.startswith(upload_prefix(job.tenant_id)):
            raise bus.PermanentError("the upload is not one of your model uploads")
        try:
            size = s3.head_object(Bucket=job.bucket, Key=job.upload_key)["ContentLength"]
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                raise bus.PermanentError(
                    f"upload not found: {job.upload_key}. Upload the file first.") from e
            raise
        if size > max_bytes:  # refuse before reading it into memory
            raise bus.PermanentError(f"the file is {size / 1024**2:.0f} MB; the limit is "
                                     f"{max_bytes / 1024**2:.0f} MB")
        data = s3.get_object(Bucket=job.bucket, Key=job.upload_key)["Body"].read()
        try:
            input_size = validate_onnx(data, job.decoder, job.classes, max_bytes)
        except ProblemsFound as e:
            raise bus.PermanentError("\n".join(e.problems)) from e
        s3.put_object(Bucket=job.bucket, Key=f"{registry.model_prefix(job.tenant_id, job.model_id)}"
                      "/model.onnx", Body=data)
        registry.register_model(s3, r, job.bucket, manifest.model_copy(
            update={"state": "ready", "input_size": input_size}), scope=job.tenant_id)
        bus.set_status(r, job.job_id, "done", tenant_id=job.tenant_id, kind="model",
                       model_id=job.model_id, input_size=input_size)
    except bus.PermanentError as e:
        registry.register_model(s3, r, job.bucket, manifest.model_copy(
            update={"report": str(e)}), scope=job.tenant_id)
        raise
