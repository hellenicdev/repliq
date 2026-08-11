from bson import ObjectId
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from ..database import get_db
from ..models.job import Job, JobStatus
from ..repositories import jobs as jobs_repo
from ..services import s3 as s3_store
from ..services.generation import process_job
from ..services.storage import get_storage_service
from ..utils.turnstile import TurnstileError, validate_turnstile

router = APIRouter(tags=["jobs"])


class GenerateRequest(BaseModel):
    sentence: str = Field(min_length=2, max_length=500)
    turnstileToken: str | None = None


@router.post("/api/generate", status_code=201)
async def generate(request: GenerateRequest, req: Request, db: AsyncIOMotorDatabase = Depends(get_db)):
    try:
        await validate_turnstile(request.turnstileToken, req.client.host if req.client else None)
    except TurnstileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job = Job(
        _id=str(ObjectId()),
        status=JobStatus.PROCESSING,
        sentence=request.sentence.strip(),
    )
    await jobs_repo.create_job(db, job)
    await process_job(db, job.id)
    return {"jobId": job.id}


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    job = await jobs_repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/api/jobs/{job_id}/output")
async def get_job_output(job_id: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    job = await jobs_repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != JobStatus.COMPLETED or not job.outputPath:
        raise HTTPException(status_code=409, detail=f"job is {job.status.value}; no output yet")

    output_path = Path(job.outputPath)
    if not output_path.is_file():
        # The local copy died with a redeploy: serve the object-storage mirror.
        if s3_store.available():
            url = s3_store.presigned_get_url(f"output/{output_path.name}", expires=3600)
            if url:
                return RedirectResponse(url)
        raise HTTPException(status_code=404, detail="output file missing")

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"dialogue-video-{job_id}.mp4",
    )
