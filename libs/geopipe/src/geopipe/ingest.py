"""Ingest of an uploaded dataset zip (ADR-0016, ADR-0017), run by the trainer stage.

The handler checks the upload, records the dataset as `ingesting`, and ends in `ready` or, on bad
input, `failed` with a report the owner can read. Bad input raises `bus.PermanentError`: retrying
cannot help.
"""

import tempfile
import time
from pathlib import Path

from botocore.exceptions import ClientError

from . import bus, dataset_zip, registry
from .messages import IngestJob


def upload_prefix(tenant_id: str) -> str:
    return f"tenants/{tenant_id}/uploads/datasets/"


def ingest(
    r, s3, job: IngestJob, now=time.time, limits: dataset_zip.Limits = dataset_zip.DEFAULT_LIMITS
) -> None:
    if not registry.ID_RE.match(job.dataset_id):
        raise bus.PermanentError(f"bad dataset id {job.dataset_id!r}")
    manifest = registry.DatasetManifest(
        id=job.dataset_id,
        name=job.name,
        classes=[],
        splits={},
        boxes=0,
        licence=job.licence,
        attribution=job.attribution,
        state="ingesting",
        created_at=now(),
    )
    try:
        bus.set_status(
            r,
            job.job_id,
            "ingesting",
            tenant_id=job.tenant_id,
            kind="ingest",
            dataset_id=job.dataset_id,
        )
        if ".." in job.upload_key or not job.upload_key.startswith(upload_prefix(job.tenant_id)):
            raise bus.PermanentError("the upload is not one of your dataset uploads")
        try:
            s3.head_object(Bucket=job.bucket, Key=job.upload_key)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                raise bus.PermanentError(
                    f"upload not found: {job.upload_key}. Upload the zip first."
                ) from e
            raise
        registry.write_manifest(s3, r, job.bucket, job.tenant_id, manifest)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            zip_path = work / "upload.zip"
            size = s3.head_object(Bucket=job.bucket, Key=job.upload_key)["ContentLength"]
            if size > limits.max_bytes:
                raise bus.PermanentError(
                    f"the upload is {size / 1024**3:.1f} GB; the limit is "
                    f"{limits.max_bytes / 1024**3:.1f} GB"
                )
            s3.download_file(job.bucket, job.upload_key, str(zip_path))
            bus.touch(r, job.job_id)
            try:
                dataset_zip.safe_extract(zip_path, work / "unzipped", limits)
                root = dataset_zip.prepare(work / "unzipped", work, limits)
            except dataset_zip.ProblemsFound as e:
                raise bus.PermanentError("\n".join(e.problems)) from e
            zip_path.unlink()
            done = registry.register_dataset(
                s3,
                r,
                job.bucket,
                root,
                job.dataset_id,
                scope=job.tenant_id,
                name=job.name,
                licence=job.licence,
                attribution=job.attribution,
                now=now,
            )
        bus.set_status(
            r,
            job.job_id,
            "done",
            tenant_id=job.tenant_id,
            kind="ingest",
            dataset_id=job.dataset_id,
            images=sum(done.splits.values()),
            boxes=done.boxes,
        )
    except bus.PermanentError as e:
        failed = manifest.model_copy(update={"state": "failed", "report": str(e)})
        registry.write_manifest(s3, r, job.bucket, job.tenant_id, failed)
        raise
