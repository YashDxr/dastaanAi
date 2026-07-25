"""Wire and persistence models for the private music API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MIN_DURATION_SECONDS = 5
MAX_DURATION_SECONDS = 60
MAX_PROMPT_CHARS = 1000


class MusicJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=3, max_length=MAX_PROMPT_CHARS)
    negative_prompt: str = Field(default="", max_length=MAX_PROMPT_CHARS)
    duration_seconds: int = Field(ge=MIN_DURATION_SECONDS, le=MAX_DURATION_SECONDS)
    seed: int | None = Field(default=None, ge=0, le=(2**31) - 1)


class MusicJobAccepted(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed", "expired"]


class MusicJobStatus(MusicJobAccepted):
    seed: int | None = None
    duration_seconds: int
    error_code: str | None = None


@dataclass(frozen=True)
class StoredMusicJob:
    job_id: str
    idempotency_key: str
    request_hash: str
    status: str
    prompt: str
    negative_prompt: str
    duration_seconds: int
    seed: int
    output_path: str | None
    output_sha256: str | None
    error_code: str | None
    attempts: int
