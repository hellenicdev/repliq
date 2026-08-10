from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_to_default(value: str | None, default: str) -> str:
    return value if value and value.strip() else default


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "dialogue_video"

    # Cloudflare Turnstile
    turnstile_secret_key: str = ""
    turnstile_enforced: bool = True

    # Groq (LLM phrase segmentation)
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # FFmpeg
    ffmpeg_path: str = ""

    # Media / storage
    media_root: Path = Path("media")
    storage_backend: str = "local"

    # CORS
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Generation limits
    max_clips: int = 12
    max_total_duration: float = 30.0
    output_width: int = 640
    output_height: int = 360
    output_fps: int = 24

    @field_validator("mongodb_uri", mode="before")
    @classmethod
    def _mongodb_uri_default(cls, v: object) -> object:
        return _empty_to_default(str(v), "mongodb://localhost:27017") if v is not None else v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def media_dir(self) -> Path:
        return self.media_root.resolve()

    @property
    def source_dir(self) -> Path:
        return self.media_dir / "source"

    @property
    def clips_dir(self) -> Path:
        return self.media_dir / "clips"

    @property
    def output_dir(self) -> Path:
        return self.media_dir / "output"


settings = Settings()
