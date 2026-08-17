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
from ..repositories import dialogue as dialogue_repo
from ..repositories import jobs as jobs_repo
from ..repositories import videos as videos_repo
from . import video as ffmpeg
from . import s3 as s3_store
from .search import NoClipsFound, get_search_service
from .segmentation import segment_sentence
from .storage import cached_source_path, get_storage_service

logger = logging.getLogger(__name__)

ALIGN_TOLERANCE = 0.15


def compute_align_ratio(video_duration: float, srt_span: float) -> float | None:
    """Ratio converting SRT timestamps to real video time.

    Subtitle files for these films are frequently timed for a different
    master (e.g. 25 fps PAL subs on a 23.976 fps film), which drifts by
    ~4% — minutes of error by the end of a feature. A constant ratio fixes
    that. Returns None when the SRT is too far from the video's length to
    be the same cut (in which case its timestamps cannot be trusted at all).
    """
    if not video_duration or video_duration <= 0 or not srt_span or srt_span <= 0:
        return None
    ratio = video_duration / srt_span
    if abs(ratio - 1.0) > ALIGN_TOLERANCE:
        return None
    return ratio


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
                if not await _align_match(db, video_doc, m):
                    return None
                if video_doc.sourceUrl and cached_source_path(video_doc) is None:
                    await _set_message(db, job.id, f"Streaming source: {video_doc.title}…")
                    return m, video_doc.sourceUrl
                source = await storage.ensure_local(video_doc)
                await _fill_video_metadata(db, video_doc, source)
                return m, source

        prepped = [
            r for r in await asyncio.gather(*(_prepare(m) for m in matches)) if r is not None
        ]
        last_error: Exception | None = None
        for i, (m, source) in enumerate(prepped):
            clip_path = settings.clips_dir / f"{job.id}_{i}.mp4"
            await _set_message(db, job.id, f"Extracting clip {i + 1}/{len(matches)}…")
            try:
                await _extract_with_fallback(db, job, m, source, clip_path, storage)
            except Exception as exc:  # noqa: BLE001 - one bad film must not sink the job
                last_error = exc
                logger.warning("skipping clip from %s: %s", m.videoTitle, exc)
                continue
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

        if not clip_paths:
            raise NoClipsFound(f"No clips could be fetched: {last_error or 'unknown error'}")

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


async def _align_match(db: AsyncIOMotorDatabase, video: Video, m) -> bool:
    """Scale the match's times from SRT time to the video's real timeline
    (frame-rate drift correction). Returns False when the SRT is for a
    different cut of the film — its timestamps would cut the wrong words,
    so the clip is dropped instead of played wrong."""
    duration = video.duration
    if not duration:
        try:
            source = cached_source_path(video) or video.sourceUrl
            if not source:
                return True
            meta = await ffmpeg.probe_metadata(source)
            duration = meta.get("duration")
            if duration:
                await videos_repo.update_video(db, video.id, duration=duration)
        except Exception as exc:  # noqa: BLE001 - probing is best-effort
            logger.warning("probe failed for %s: %s", video.title, exc)
            return True
    if not duration:
        return True

    srt_span = await dialogue_repo.max_end_time(db, video.id)
    ratio = compute_align_ratio(duration, srt_span) if srt_span else None
    if srt_span and ratio is None:
        logger.warning(
            "skipping %s: SRT length %.0fs does not match video %.0fs",
            video.title,
            srt_span,
            duration,
        )
        return False
    if ratio and abs(ratio - 1.0) > 0.005:
        m.startTime = round(m.startTime * ratio, 3)
        m.endTime = round(m.endTime * ratio, 3)
        logger.info("aligned %s clip times by x%.4f", video.title, ratio)
    return True


async def _extract_with_fallback(
    db: AsyncIOMotorDatabase, job: Job, m, source: Path | str, clip_path: Path, storage
) -> None:
    """Extract a clip, retrying the stream once, then falling back to a full
    download when the source is a URL. Raises when all paths fail."""
    try:
        await ffmpeg.extract_clip(source, m.startTime, m.endTime, clip_path)
        return
    except ffmpeg.FFmpegError:
        if not isinstance(source, str):
            raise
        logger.warning("stream extract failed for %s, retrying once", m.videoId)
        try:
            await ffmpeg.extract_clip(source, m.startTime, m.endTime, clip_path)
            return
        except ffmpeg.FFmpegError:
            pass
        video_doc = await videos_repo.get_video(db, m.videoId)
        await _set_message(db, job.id, f"Fetching source: {video_doc.title} (first use)…")
        source = await storage.ensure_local(video_doc)
        await _fill_video_metadata(db, video_doc, source)
        await ffmpeg.extract_clip(source, m.startTime, m.endTime, clip_path)


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
