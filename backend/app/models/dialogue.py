from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Dialogue(BaseModel):
    """A single dialogue segment with known start/end timestamps."""

    id: str = Field(alias="_id")
    videoId: str
    character: str = "Unknown"
    text: str
    startTime: float
    endTime: float
    confidence: float | None = None
    embedding: list[float] | None = None  # reserved for Phase 4
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"populate_by_name": True}

    @property
    def duration(self) -> float:
        return max(0.0, self.endTime - self.startTime)
