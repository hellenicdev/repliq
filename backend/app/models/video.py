from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class Video(BaseModel):
    """A source video in the database. Actual file bytes live in object storage
    (or the local media/ directory during development), never in MongoDB.

    Remote sources (sourceUrl set) are fetched lazily on first use and cached
    locally — nothing is downloaded at index time.
    """

    id: str = Field(alias="_id")
    title: str
    source: str = "local"
    fileUrl: str
    sourceUrl: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    createdAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"populate_by_name": True}

    @field_validator("fps", mode="before")
    @classmethod
    def _fps_to_int(cls, v: object) -> object:
        if isinstance(v, float):
            return round(v)
        return v
