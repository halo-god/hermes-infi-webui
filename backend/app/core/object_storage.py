"""S3-compatible object storage (MinIO in production).

Synchronous boto3 client — callers wrap calls in asyncio.to_thread. Default
backend is 'db' (content inline in Postgres); set STORAGE_BACKEND=minio to
offload workspace artifacts to MinIO/S3.
"""
from __future__ import annotations

import threading

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings

_client = None
_lock = threading.Lock()
_bucket_ready = False


def reset_client() -> None:
    """Drop the cached client/bucket flag (tests use this after mutating settings)."""
    global _client, _bucket_ready
    _client = None
    _bucket_ready = False


def _get_client():
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = boto3.client(
                    "s3",
                    endpoint_url=settings.minio_endpoint or None,
                    aws_access_key_id=settings.minio_access_key,
                    aws_secret_access_key=settings.minio_secret_key,
                    config=Config(signature_version="s3v4"),
                    region_name=settings.minio_region,
                )
    return _client


def _ensure_bucket() -> None:
    global _bucket_ready
    if _bucket_ready:
        return
    client = _get_client()
    bucket = settings.minio_bucket
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        try:
            client.create_bucket(Bucket=bucket)
        except ClientError:
            pass
    _bucket_ready = True


def put(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    _ensure_bucket()
    _get_client().put_object(
        Bucket=settings.minio_bucket, Key=key, Body=data, ContentType=content_type
    )
    return key


def get(key: str) -> bytes:
    obj = _get_client().get_object(Bucket=settings.minio_bucket, Key=key)
    return obj["Body"].read()


def presigned_url(key: str, expires: int = 3600) -> str:
    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.minio_bucket, "Key": key},
        Expires=expires,
    )


def delete(key: str) -> None:
    """Delete an object from storage."""
    _ensure_bucket()
    _get_client().delete_object(Bucket=settings.minio_bucket, Key=key)
