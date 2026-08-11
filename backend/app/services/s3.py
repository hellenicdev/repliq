"""S3-compatible object storage (Backblaze B2 / Cloudflare R2).

Both providers speak the S3 API, so switching between them is a config
change (S3_ENDPOINT + region + keys + bucket), never a code change.

Keys stored here:
  source/<filename>  - cached source films (downloaded from archive.org once)
  output/<job_id>.mp4 - finished generation results
"""

import asyncio
import logging
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..config import settings

logger = logging.getLogger(__name__)

_client = None


def available() -> bool:
    """True when S3 credentials are configured (regardless of STORAGE_BACKEND)."""
    return bool(
        settings.s3_endpoint
        and settings.s3_access_key_id
        and settings.s3_secret_access_key
        and settings.s3_bucket
    )


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region or "us-east-1",
            config=Config(signature_version="s3v4", retries={"max_attempts": 4}),
        )
    return _client


async def head_object(key: str) -> int | None:
    """Return the object size in bytes, or None if it does not exist."""
    try:
        resp = await asyncio.to_thread(_get_client().head_object, Bucket=settings.s3_bucket, Key=key)
        return int(resp["ContentLength"])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


async def upload_file(key: str, path: Path) -> None:
    await asyncio.to_thread(_get_client().upload_file, str(path), settings.s3_bucket, key)


async def download_file(key: str, path: Path) -> None:
    await asyncio.to_thread(_get_client().download_file, settings.s3_bucket, key, str(path))


def presigned_get_url(key: str, expires: int = 3600) -> str:
    return _get_client().generate_presigned_url(
        "get_object", Params={"Bucket": settings.s3_bucket, "Key": key}, ExpiresIn=expires
    )