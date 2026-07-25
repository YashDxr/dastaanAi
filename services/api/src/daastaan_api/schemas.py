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


# --- exports ---------------------------------------------------------------


class ExportRequest(BaseModel):
    format: str = Field(max_length=8)


class ExportOut(BaseModel):
    format: str
    ready: bool
    url: str | None
    size_bytes: int | None


class ExportFormatOut(ExportOut):
    label: str
    content_type: str
    detail: str
    recommended: bool


# --- document ingest -------------------------------------------------------


class IngestAccepted(BaseModel):
    ingest_id: str
    filename: str


class IngestOut(BaseModel):
    id: str
    filename: str
    status: str
    method: str | None
    page_count: int | None
    raw_chars: int | None
    cleaned_text: str | None
    title_hint: str | None
    genre_hint: str | None
    notes: str | None
    error: str | None


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
    # The stages this particular run covers. A full generation lists all of them;
    # a scoped regeneration lists only the slice it re-executes, so the client can
    # show completion against the right denominator.
    planned_stages: list[str]
    jobs: list[JobOut]


# --- feedback and regeneration ---------------------------------------------


class FeedbackRequest(BaseModel):
    """Free text. The Feedback Interpreter turns it into a directive, which is
    then re-validated server-side before anything is dispatched."""

    raw_text: str = Field(min_length=3, max_length=limits.MAX_FEEDBACK_CHARS)
    target_id: str | None = Field(default=None, max_length=64)


class FeedbackOut(BaseModel):
    id: str
    raw_text: str
    status: str
    error: str | None
    directive_json: dict[str, Any] | None
    resulting_version_id: str | None
    created_at: datetime


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


# --- mysteries -------------------------------------------------------------

class CreateMysteryRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    premise: str = Field(min_length=20, max_length=6000)
    tone: Literal["noir", "cozy", "thriller", "supernatural", "classic_whodunit"] = "classic_whodunit"
    setting: str = Field(min_length=2, max_length=240)
    suspect_count: int = Field(default=4, ge=3, le=8)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    duration_minutes: int = Field(default=20, ge=5, le=90)
    victim_name: str | None = Field(default=None, max_length=100)
    suspect_names: list[str] = Field(default_factory=list, max_length=8)


class InterrogateMysteryRequest(BaseModel):
    suspect_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=2, max_length=800)


class AccuseMysteryRequest(BaseModel):
    suspect_id: str = Field(min_length=1, max_length=64)


class MysteryActionOut(BaseModel):
    status: str
    message: str
    case: dict[str, Any]


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
    # What the cache hits would have cost. Inferred from the average price of
    # comparable paid calls, so it is an estimate rather than a ledger figure.
    cache_savings_usd: float = 0.0
    cache_hits: int = 0
    by_stage: list[CostRow]
    by_model: list[CostRow]


class RoleUpdate(BaseModel):
    role: str


# --- admin analytics --------------------------------------------------------


class RunSummaryOut(BaseModel):
    """One `StoryVersion` execution, rolled up from jobs and the cost ledger."""

    version_id: str
    story_id: str
    story_title: str | None
    user_id: str | None
    user_email: str | None
    version_number: int
    genre: str | None
    mood: str | None
    is_regen: bool
    regen_scope: str | None
    regen_stage: str | None
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    stage_count: int
    calls: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_hits: int
    error: str | None


class RunStageOut(BaseModel):
    stage: str
    status: str
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    calls: int
    cache_hits: int


class ModelCostOut(BaseModel):
    label: str
    calls: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_hits: int


class AssetCountOut(BaseModel):
    kind: str
    count: int
    duration_ms: int


class RunDetailOut(BaseModel):
    version_id: str
    story_id: str
    story_title: str | None
    user_id: str | None
    user_email: str | None
    version_number: int
    genre: str | None
    mood: str | None
    parent_version_id: str | None
    is_regen: bool
    directive: dict[str, Any] | None
    feedback_text: str | None
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    calls: int
    cache_hits: int
    stages: list[RunStageOut]
    by_model: list[ModelCostOut]
    assets: list[AssetCountOut]


class UserSummaryOut(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime
    stories: int
    calls: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_hits: int
    last_active_at: datetime | None


class DailySpendOut(BaseModel):
    day: str
    cost_usd: float
    calls: int


class StorySpendOut(BaseModel):
    story_id: str
    title: str | None
    cost_usd: float
    calls: int


class UserCostDetailOut(BaseModel):
    user: UserSummaryOut
    daily: list[DailySpendOut]
    by_stage: list[CostRow]
    by_model: list[CostRow]
    by_story: list[StorySpendOut]
    cache_savings_usd: float


StoryDetailOut.model_rebuild()
