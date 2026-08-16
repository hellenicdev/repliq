"""Storage abstraction.

Phase 1 uses the local filesystem (LocalStorageService). The interface is
kept small so an S3-compatible backend (Backblaze B2 / Cloudflare R2) can be
added without changing callers.

Remote videos are fetched lazily: index_pd_film.py stores only metadata +
a sourceUrl; the actual file is downloaded into the local cache the first
time a generation needs it (ensure_local). When S3 is configured, fetched
films are also mirrored to object storage once, so later instances /
redeploys re-download them from there instead of archive.org.
"""

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import settings
from ..models.video import Video
from . import s3 as s3_store

logger = logging.getLogger(__name__)

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _mirror_later(remote_key: str, path: Path) -> None:
    """Upload a cached film to object storage in the background so generation
    never waits on the mirror (a 500 MB upload can take minutes)."""
    async def _upload() -> None:
        try:
            await s3_store.upload_file(remote_key, path)
            logger.info("mirrored %s to object storage", remote_key)
        except Exception as exc:  # noqa: BLE001 - cache is best-effort
            logger.warning("s3 mirror failed for %s: %s", remote_key, exc)

    asyncio.get_running_loop().create_task(_upload())


class StorageError(Exception):
    pass


class StorageService(ABC):
    @abstractmethod
    def resolve_source(self, file_url: str) -> Path:
        """Resolve a video's local fileUrl to a file path."""
        raise NotImplementedError

    @abstractmethod
    async def ensure_local(self, video: Video) -> Path:
        """Return a local path for the video, downloading it (and caching)
        from sourceUrl on first use. Local videos pass straight through."""
        raise NotImplementedError

    @abstractmethod
    def output_path(self, job_id: str) -> Path:
        raise NotImplementedError

    @abstractmethod
    def output_url(self, job_id: str) -> str:
        """Public URL where the finished video can be fetched."""
        raise NotImplementedError


def cached_source_path(video: Video) -> Path | None:
    """Return the local cache path for a remote video, or None when the
    film has not been downloaded yet."""
    if not video.sourceUrl:
        return None
    filename = _FILENAME_SAFE.sub("_", Path(urlparse(video.sourceUrl).path).name) or "video.mp4"
    p = settings.source_dir / filename
    return p if p.is_file() and p.stat().st_size > 0 else None


async def _ensure_local_file(video: Video) -> Path:
    """Shared fetch-and-cache logic for both storage backends.

    Order of preference: local cache -> S3 mirror -> archive.org origin.
    Fetched films are mirrored to object storage once so redeploys never
    hit archive.org again.
    """
    if not video.sourceUrl:
        p = Path(video.fileUrl)
        return p if p.is_absolute() else p.resolve()

    filename = _FILENAME_SAFE.sub("_", Path(urlparse(video.sourceUrl).path).name) or "video.mp4"
    target = settings.source_dir / filename
    if target.is_file() and target.stat().st_size > 0:
        return target

    settings.source_dir.mkdir(parents=True, exist_ok=True)
    part = settings.source_dir / f"{filename}.part"

    remote_key = f"source/{filename}"

    if s3_store.available():
        try:
            remote_size = await s3_store.head_object(remote_key)
            if remote_size is not None and remote_size > 0:
                logger.info("downloading %s from object storage", remote_key)
                part.unlink(missing_ok=True)
                await s3_store.download_file(remote_key, part)
                if part.stat().st_size == remote_size:
                    part.rename(target)
                    return target
                part.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - fall back to the origin URL
            logger.warning("s3 fetch failed for %s, falling back to origin: %s", remote_key, exc)

    timeout = httpx.Timeout(60, read=300)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            existing = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={existing}-"} if existing else {}
            mode = "ab" if existing else "wb"
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                async with client.stream("GET", video.sourceUrl, headers=headers) as resp:
                    if resp.status_code not in (200, 206):
                        raise StorageError(
                            f"failed to fetch {video.sourceUrl} (HTTP {resp.status_code})"
                        )
                    if resp.status_code == 200 and existing:
                        existing = 0
                        mode = "wb"
                    with part.open(mode) as fh:
                        async for chunk in resp.aiter_bytes(1024 * 1024):
                            fh.write(chunk)
            part.rename(target)
            if s3_store.available():
                _mirror_later(remote_key, target)
            return target
        except Exception as exc:  # noqa: BLE001 - retry with a fresh mirror connection
            last_error = exc
            logger.warning("download attempt %d failed for %s: %s", attempt + 1, video.title, exc)
            part.unlink(missing_ok=True)

    raise StorageError(f"could not download {video.sourceUrl}: {last_error}")


class LocalStorageService(StorageService):
    def resolve_source(self, file_url: str) -> Path:
        p = Path(file_url)
        return p if p.is_absolute() else p.resolve()

    async def ensure_local(self, video: Video) -> Path:
        return await _ensure_local_file(video)

    def output_path(self, job_id: str) -> Path:
        return settings.output_dir / f"{job_id}.mp4"

    def output_url(self, job_id: str) -> str:
        return f"/api/jobs/{job_id}/output"


class S3StorageService(StorageService):
    """S3-primary storage: finished videos are served from R2 via presigned
    URLs; source films are cached locally for FFmpeg but always re-fetchable
    from the R2 mirror."""

    def resolve_source(self, file_url: str) -> Path:
        p = Path(file_url)
        return p if p.is_absolute() else p.resolve()

    async def ensure_local(self, video: Video) -> Path:
        return await _ensure_local_file(video)

    def output_path(self, job_id: str) -> Path:
        return settings.output_dir / f"{job_id}.mp4"

    def output_url(self, job_id: str) -> str:
        url = s3_store.presigned_get_url(f"output/{job_id}.mp4", expires=3600)
        if url:
            return url
        return f"/api/jobs/{job_id}/output"


def get_storage_service() -> StorageService:
    if settings.storage_backend == "local":
        return LocalStorageService()
    if settings.storage_backend == "s3":
        return S3StorageService()
    raise ValueError(f"unsupported STORAGE_BACKEND: {settings.storage_backend}")


def prune_stale_cache() -> None:
    """Delete cached files older than their TTL so the disk stays bounded.

    Source films are re-fetched on demand (archive.org, or the S3 mirror
    while it holds a copy), which is the intended fetch-and-cache-for-a-
    few-days behavior.
    """
    import time

    now = time.time()
    ttl_source = settings.cache_ttl_days * 86400
    ttl_output = 7 * 86400  # finished MP4s live in object storage; keep local copies a bit longer
    for directory, ttl in (
        (settings.source_dir, ttl_source),
        (settings.clips_dir, ttl_source),
        (settings.output_dir, ttl_output),
    ):
        if not directory.is_dir():
            continue
        for f in directory.glob("*"):
            try:
                if now - f.stat().st_mtime > ttl:
                    f.unlink(missing_ok=True)
                    logger.info("pruned stale cache file %s", f)
            except OSError:
                continue
