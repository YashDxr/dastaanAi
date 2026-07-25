"""Request and response DTOs.

These drive FastAPI's generated OpenAPI schema, which `openapi-typescript` turns
into the frontend types - so Pydantic stays the single source of truth for the
contract and neither React app hand-writes an interface.
"""

from datetime import datetime
from typing import Any, Literal

from daastaan_contracts import Scope, StageName, limits
from pydantic import BaseModel, EmailStr, Field

# --- auth ------------------------------------------------------------------


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime


# --- stories ---------------------------------------------------------------


class CreateStoryRequest(BaseModel):
    raw_text: str = Field(min_length=20, max_length=limits.MAX_STORY_INPUT_CHARS)
    genre_hint: str | None = Field(default=None, max_length=60)
    language: str = Field(default="en", min_length=2, max_length=5, pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")
    output_format: Literal["audio", "video", "both"] = "audio"


class StoryOut(BaseModel):
    id: str
    title: str | None
    status: str
    current_version_id: str | None
    created_at: datetime


class VersionOut(BaseModel):
    id: str
    story_id: str
    parent_version_id: str | None
    version_number: int
    genre: str | None
    mood: str | None
    created_at: datetime


class StoryDetailOut(BaseModel):
    story: StoryOut
    version: VersionOut | None
    state: dict[str, Any] | None
    assets: list["AssetOut"]


class AssetOut(BaseModel):
    id: str
    kind: str
    line_id: str | None
    scene_id: str | None
    content_type: str
    duration_ms: int | None
    url: str


# --- progress --------------------------------------------------------------


class JobOut(BaseModel):
    stage: str
    status: str
    attempt: int
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ProgressOut(BaseModel):
    story_id: str
    version_id: str | None
    status: str
    jobs: list[JobOut]


# --- feedback and regeneration ---------------------------------------------


class FeedbackRequest(BaseModel):
    """Free text. The Feedback Interpreter turns it into a directive, which is
    then re-validated server-side before anything is dispatched."""

    raw_text: str = Field(min_length=3, max_length=limits.MAX_FEEDBACK_CHARS)
    target_id: str | None = Field(default=None, max_length=64)


class RegenerateRequest(BaseModel):
    """The explicit, button-driven path. Deterministic, so it is the safe route
    to use during a live demo."""

    scope: Scope
    target_stage: StageName
    target_id: str | None = Field(default=None, max_length=64)
    instruction_delta: str = Field(default="", max_length=limits.MAX_FEEDBACK_CHARS)


class DispatchAccepted(BaseModel):
    story_id: str
    version_id: str
    stages: list[str]
    task_id: str


# --- admin -----------------------------------------------------------------


class AdminSettingIn(BaseModel):
    value: dict[str, Any]


class AdminSettingOut(BaseModel):
    key: str
    value: dict[str, Any]
    updated_by: str | None
    updated_at: datetime


class CostRow(BaseModel):
    label: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class CostSummaryOut(BaseModel):
    total_usd: float
    budget_cap_usd: float
    remaining_usd: float
    by_stage: list[CostRow]
    by_model: list[CostRow]


class RoleUpdate(BaseModel):
    role: str


StoryDetailOut.model_rebuild()
