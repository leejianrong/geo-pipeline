"""S3-API access only (ADR-0008): endpoint and credentials come from the environment."""

import os
import time

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError, EndpointConnectionError


def client(endpoint: str | None = None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint or os.environ.get("S3_ENDPOINT", "http://localhost:8333"),
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket(s3, name: str, retries: int = 30) -> None:
    """Create the bucket if missing, waiting for the store to come up."""
    for _ in range(retries):
        try:
            s3.head_bucket(Bucket=name)
            return
        except ClientError:
            try:
                s3.create_bucket(Bucket=name)
                return
            except (ClientError, EndpointConnectionError):
                pass
        except EndpointConnectionError:
            pass
        time.sleep(1)
    raise RuntimeError(f"object store not reachable, cannot ensure bucket {name!r}")


def presign_get(s3, bucket: str, key: str, expires: int = 3600) -> str:
    return s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                     ExpiresIn=expires)
