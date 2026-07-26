"""Request and response DTOs.

These drive FastAPI's generated OpenAPI schema, which `openapi-typescript` turns
into the frontend types - so Pydantic stays the single source of truth for the
contract and neither React app hand-writes an interface.
"""

from datetime import datetime
from typing import Any, Literal

from daastaan_contracts import ConsistencyCheckStatus, ConsistencyFinding, ReviewAction, ReviewStatus, Scope, StageName, VideoEditManifest, limits
from pydantic import BaseModel, EmailStr, Field, model_validator

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
    language: str = Field(
        default="en",
        min_length=2,
        max_length=5,
        pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$",
    )
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


class StageProgressOut(BaseModel):
    """How far through a fan-out stage this run is.

    The same numbers the `stage_progress` event carries, recomputed from
    `media_assets` so the polling fallback agrees with the live stream instead of
    dropping back to an all-or-nothing stage chip when the stream is unavailable.
    """

    stage: str
    completed: int
    total: int


class ProgressOut(BaseModel):
    story_id: str
    version_id: str | None
    status: str
    # The stages this particular run covers. A full generation lists all of them;
    # a scoped regeneration lists only the slice it re-executes, so the client can
    # show completion against the right denominator.
    planned_stages: list[str]
    jobs: list[JobOut]
    # Only the fan-out stages appear here, and only once their work is known.
    stage_progress: list[StageProgressOut] = []


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
    # A Story Time Machine branch can intentionally start from an older
    # revision. The route verifies that this version belongs to the owned story;
    # callers cannot use it to read or fork another user's state.
    base_version_id: str | None = Field(default=None, max_length=64)
    # Browser clients send the current pointer they last rendered. It is an
    # optimistic-concurrency guard, separate from ``base_version_id`` so an
    # intentional branch from v1 can still become a sibling of current v3.
    expected_current_version_id: str | None = Field(default=None, max_length=64)


class DispatchAccepted(BaseModel):
    story_id: str
    version_id: str
    stages: list[str]
    task_id: str


# --- video editor -----------------------------------------------------------


class CaptionCueOut(BaseModel):
    """One caption, timed against the untrimmed episode.

    The editor needs these to draw its own preview overlay, so the browser is
    working from the same timeline the renderer will use rather than guessing at
    line boundaries from durations it would have to add up itself.
    """

    line_id: str
    scene_id: str
    index: int
    start_ms: int
    # Where the picture changes, which is the spoken part plus its trailing pause.
    end_ms: int
    # Where the caption clears, which is the spoken part only.
    caption_end_ms: int
    speaker: str
    text: str
    image_url: str | None


class VideoEditOut(BaseModel):
    id: str
    story_id: str
    version_id: str
    name: str
    manifest: VideoEditManifest
    status: str
    error: str | None
    # The rendered MP4, when there is one. Present even while the cut is a draft
    # again, so the previous export stays downloadable while a new one is made.
    video_url: str | None
    download_url: str | None
    size_bytes: int | None
    duration_ms: int | None
    # False once the manifest has been edited past what was rendered.
    render_current: bool
    # False when the story has been regenerated since this cut was made. The cut
    # still renders - its footage is still there - but its trim points at a
    # timeline the current episode no longer has, so the editor archives it rather
    # than reopening it over the wrong script.
    version_current: bool
    share_url: str | None
    share_expires_at: datetime | None
    share_views: int
    created_at: datetime
    updated_at: datetime


class VideoEditCreateRequest(BaseModel):
    name: str = Field(default="", max_length=80)
    # Omitted means "start from the defaults", which is what the New cut button
    # sends. A preset name is applied by the client, not here.
    manifest: VideoEditManifest | None = None


class VideoEditUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    manifest: VideoEditManifest | None = None


class CaptionPresetOut(BaseModel):
    key: str
    label: str
    detail: str
    style: dict[str, Any]


class FontOptionOut(BaseModel):
    key: str
    label: str
    detail: str


class AspectOptionOut(BaseModel):
    key: str
    label: str
    detail: str
    width: int
    height: int


class EditorCapabilitiesOut(BaseModel):
    """What this particular episode lets the editor do.

    A cut is rebuilt from source assets, so the controls that are meaningful
    depend on which of those still exist. Reporting it here keeps the UI from
    offering a switch that the renderer would then ignore.
    """

    has_score: bool
    has_artwork: bool
    line_count: int
    duration_ms: int


class VideoEditorOut(BaseModel):
    """Everything the editor needs to open, in one request."""

    story_id: str
    version_id: str
    title: str | None
    capabilities: EditorCapabilitiesOut
    cues: list[CaptionCueOut]
    # The pipeline's own video, if it made one. Shown as "before" next to a cut.
    source_video_url: str | None
    episode_audio_url: str | None
    edits: list[VideoEditOut]
    local_audio: list["LocalAudioOut"]
    caption_presets: list[CaptionPresetOut]
    fonts: list[FontOptionOut]
    aspects: list[AspectOptionOut]


class LocalAudioOut(BaseModel):
    id: str
    filename: str
    content_type: str
    duration_ms: int | None
    size_bytes: int | None
    url: str


class ShareRequest(BaseModel):
    # Zero means no expiry. Capped so a link cannot be minted to outlive the
    # account that made it by years.
    expires_in_hours: int = Field(default=168, ge=0, le=24 * 90)


class ShareOut(BaseModel):
    share_url: str
    expires_at: datetime | None


class SharedCutOut(BaseModel):
    """The public view of a shared cut.

    Deliberately thin: a title, the video, and how long it runs. No ids, no
    owner, no story text - a share link is a link to one clip, not a window onto
    the account that made it.
    """

    title: str | None
    name: str
    duration_ms: int | None
    aspect: str
    video_url: str
    expires_at: datetime | None


# --- Plot Hole Hunter ------------------------------------------------------


class ConsistencyCheckOut(BaseModel):
    """Safe projection of one read-only continuity review.

    Findings are already strict-schema validated and reference-checked by the
    worker. The API still validates them through this DTO before returning them
    so a malformed database value can never become arbitrary client content.
    """

    id: str
    story_id: str
    version_id: str
    status: ConsistencyCheckStatus
    task_id: str | None
    summary: str | None
    findings: list[ConsistencyFinding]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


# --- admin -----------------------------------------------------------------


class AdminSettingIn(BaseModel):
    value: dict[str, Any]
    # A short operator note makes an otherwise opaque runtime change useful in
    # the audit trail without ever accepting secrets or arbitrary free-form
    # configuration as a separate field.
    reason: str | None = Field(default=None, max_length=500)


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


class StoryReviewRequest(BaseModel):
    """An intentionally narrow set of reversible editorial transitions."""

    action: ReviewAction
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def require_actionable_note(self) -> "StoryReviewRequest":
        self.note = self.note.strip() if self.note else None
        if self.action in {ReviewAction.FLAG, ReviewAction.CHANGES_REQUESTED} and not self.note:
            raise ValueError("a note is required when flagging or requesting changes")
        return self


class StoryReviewOut(BaseModel):
    status: ReviewStatus
    note: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None


class AdminStoryOwnerOut(BaseModel):
    id: str
    email: str


class AssetCoverageOut(BaseModel):
    """One asset family measured against what the current version needs."""

    kind: str
    expected: int
    complete: int
    missing: int
    placeholders: int
    required: bool


class StoryQualityOut(BaseModel):
    version_id: str | None
    state_available: bool
    # ``ready_for_review`` is an asset-quality gate, not a publication action.
    # It is safe to inspect a flagged story; approval is still an explicit write.
    ready_for_review: bool
    required_missing: int
    placeholder_count: int
    coverage: list[AssetCoverageOut]
    warnings: list[str]


class AdminStorySummaryOut(BaseModel):
    id: str
    title: str | None
    status: str
    current_version_id: str | None
    flagged: bool
    created_at: datetime
    owner: AdminStoryOwnerOut | None
    review: StoryReviewOut
    quality: StoryQualityOut


class AuditLogOut(BaseModel):
    id: str
    actor_user_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    metadata: dict[str, Any] | None
    created_at: datetime


class AdminStoryDetailOut(AdminStorySummaryOut):
    version: VersionOut | None
    # Admins need the script/state to make an editorial decision. This endpoint
    # remains behind ``require_admin`` and never appears in listener responses.
    state: dict[str, Any] | None
    assets: list[AssetOut]
    review_history: list[AuditLogOut]


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
VideoEditorOut.model_rebuild()
