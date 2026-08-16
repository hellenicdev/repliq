"""Orchestrates the full pipeline: search -> fetch source -> extract -> concat -> record.

Keep this independent of HTTP: routes call process_job() and the job doc
is the only shared state, so moving execution to a background worker later
requires no changes here. Remote source films are downloaded lazily on
first use (see StorageService.ensure_local).
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..config import settings
from ..models.job import ClipRef, Job, JobStatus
from ..models.video import Video
from ..repositories import jobs as jobs_repo
from ..repositories import videos as videos_repo
from . import video as ffmpeg
from . import s3 as s3_store
from .search import NoClipsFound, get_search_service
from .segmentation import segment_sentence
from .storage import get_storage_service

logger = logging.getLogger(__name__)


async def process_job(db: AsyncIOMotorDatabase, job_id: str) -> Job:
    """Execute a pending job, updating its document as work progresses."""
    job = await jobs_repo.get_job(db, job_id)
    if job is None:
        raise ValueError(f"job not found: {job_id}")

    try:
        await _run_generation(db, job)
        await jobs_repo.update_job(
            db,
            job_id,
            status=JobStatus.COMPLETED.value,
            message=None,
            completedAt=datetime.now(timezone.utc),
        )
    except NoClipsFound as exc:
        await jobs_repo.update_job(db, job_id, status=JobStatus.FAILED.value, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - record anything so the client sees a clear error
        logger.exception("generation failed for job %s", job_id)
        await jobs_repo.update_job(db, job_id, status=JobStatus.FAILED.value, error=str(exc))
    return await jobs_repo.get_job(db, job_id)


async def _set_message(db: AsyncIOMotorDatabase, job_id: str, message: str) -> None:
    await jobs_repo.update_job(db, job_id, message=message)


async def _run_generation(db: AsyncIOMotorDatabase, job: Job) -> None:
    search = get_search_service()

    await _set_message(db, job.id, "Segmenting your sentence…")
    phrases = await segment_sentence(job.sentence)
    logger.info("segmented %r -> %r", job.sentence, phrases)

    await _set_message(db, job.id, "Searching for dialogue…")
    matches = await search.search_phrases(db, phrases, limit=settings.max_clips)
    if not matches:
        raise NoClipsFound(
            f"No matching dialogue found for: \"{job.sentence}\". "
            "Try words from the indexed films, e.g. \"leave\", \"help\", \"run\"."
        )

    storage = get_storage_service()
    settings.clips_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    clip_paths: list[Path] = []
    clips_meta: list[ClipRef] = []
    try:
        semaphore = asyncio.Semaphore(3)

        async def _prepare(m):
            async with semaphore:
                video_doc = await videos_repo.get_video(db, m.videoId)
                if video_doc is None:
                    raise ffmpeg.FFmpegError(f"video record missing for {m.videoId}")
                if video_doc.sourceUrl and not _is_cached(video_doc):
                    await _set_message(db, job.id, f"Fetching source: {video_doc.title} (first use)…")
                source = await storage.ensure_local(video_doc)
                await _fill_video_metadata(db, video_doc, source)
                return m, source

        prepped = await asyncio.gather(*(_prepare(m) for m in matches))
        for i, (m, source) in enumerate(prepped):
            clip_path = settings.clips_dir / f"{job.id}_{i}.mp4"
            await _set_message(db, job.id, f"Extracting clip {i + 1}/{len(matches)}…")
            await ffmpeg.extract_clip(source, m.startTime, m.endTime, clip_path)
            clip_paths.append(clip_path)
            clips_meta.append(
                ClipRef(
                    videoId=m.videoId,
                    videoTitle=m.videoTitle,
                    character=m.character,
                    text=m.text,
                    startTime=m.startTime,
                    endTime=m.endTime,
                    score=round(m.score, 4),
                )
            )

        output_path = storage.output_path(job.id)
        await _set_message(db, job.id, "Concatenating clips…")
        await ffmpeg.concat_clips(clip_paths, output_path)

        # Mirror the finished video to object storage so it survives redeploys.
        if s3_store.available():
            try:
                await s3_store.upload_file(f"output/{job.id}.mp4", output_path)
            except Exception as exc:  # noqa: BLE001 - cache is best-effort
                logger.warning("s3 upload failed for job %s: %s", job.id, exc)

        await jobs_repo.update_job(
            db,
            job.id,
            clips=[c.model_dump() for c in clips_meta],
            outputPath=str(output_path),
            outputUrl=storage.output_url(job.id),
        )
    finally:
        for p in clip_paths:
            p.unlink(missing_ok=True)


def _is_cached(video: Video) -> bool:
    filename = Path(video.fileUrl).name
    p = settings.source_dir / filename
    return p.is_file() and p.stat().st_size > 0


async def _fill_video_metadata(db: AsyncIOMotorDatabase, video: Video, path: Path) -> None:
    if video.width is not None:
        return
    meta = await ffmpeg.probe_metadata(path)
    await videos_repo.update_video(
        db,
        video.id,
        duration=meta["duration"],
        width=meta["width"],
        height=meta["height"],
        fps=meta["fps"],
    )
