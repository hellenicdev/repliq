from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ClipRef(BaseModel):
    """Metadata about one clip selected for a job."""

    videoId: str
    videoTitle: str
    character: str
    text: str
    startTime: float
    endTime: float
    score: float


class Job(BaseModel):
    id: str = Field(alias="_id")
    status: JobStatus
    sentence: str
    clips: list[ClipRef] = []
    outputUrl: str | None = None
    outputPath: str | None = None
    message: str | None = None
    error: str | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completedAt: datetime | None = None

    model_config = {"populate_by_name": True}
