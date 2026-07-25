"""Settings for the native macOS music sidecar.

This service intentionally has no Dastaan database, Redis, object-storage, or
provider credentials.  It needs only a local writable work directory, the MLX
CLI location, and credentials used by the media worker to authenticate.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MusicServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        # `.env.music` is deliberately separate from the shared Compose `.env`.
        # It is loaded only by this native process and worker-music, never by
        # the API or other workers.
        env_file=(".env", ".env.music"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    service_token: str | None = Field(default=None, validation_alias="MUSIC_SERVICE_TOKEN")
    service_hmac_secret: str | None = Field(
        default=None,
        validation_alias="MUSIC_SERVICE_HMAC_SECRET",
    )
    data_dir: Path = Field(
        default=Path(".music-service"),
        validation_alias="MUSIC_SERVICE_DATA_DIR",
    )
    runner: Literal["mlx_cli", "mock"] = Field(
        default="mlx_cli",
        validation_alias="MUSIC_SERVICE_RUNNER",
    )
    sa3_path: str | None = Field(default=None, validation_alias="MUSIC_SERVICE_SA3_PATH")
    job_timeout_seconds: int = Field(
        default=600,
        ge=10,
        le=900,
        validation_alias="MUSIC_SERVICE_JOB_TIMEOUT_SECONDS",
    )
    max_queue: int = Field(default=3, ge=1, le=20, validation_alias="MUSIC_SERVICE_MAX_QUEUE")
    output_retention_seconds: int = Field(
        default=3600,
        ge=60,
        le=86_400,
        validation_alias="MUSIC_SERVICE_OUTPUT_RETENTION_SECONDS",
    )
    signature_max_age_seconds: int = Field(
        default=300,
        ge=30,
        le=900,
        validation_alias="MUSIC_SERVICE_SIGNATURE_MAX_AGE_SECONDS",
    )

    @property
    def jobs_db_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "outputs"


@lru_cache
def get_music_settings() -> MusicServiceSettings:
    return MusicServiceSettings()
