"""SQLModel tables.

Storage strategy: `StoryVersion.state_json` holds the full `StoryState` and is the
source of truth for pipeline output. Only the things that need relational access
get their own table - media assets (streaming plus the idempotency guard), jobs
(progress), cost (aggregation), and the audit and moderation surfaces. That avoids
maintaining a normalised mirror of every agent output under time pressure.

Enum-valued columns are plain `str` on purpose: native Postgres enums would need
an ALTER TYPE every time a stage is added, which is the wrong trade during a build.
Validate against the `daastaan_contracts` enums at the boundaries instead.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


def _json_column(nullable: bool = False) -> Column:
    """A JSONB column that notices in-place edits.

    Without `MutableDict`, SQLAlchemy compares the dict against itself on flush,
    finds no change, and drops the write. Every `state_json["key"] = value` in
    the codebase was silently discarded - including the `version_id` and `regen`
    a forked version is stamped with - so regenerations wrote their output back
    onto the parent version and never saw their own directive.
    """
    return Column(MutableDict.as_mutable(JSONB), nullable=nullable)


def _json_list_column(nullable: bool = False) -> Column:
    """JSONB list variant used by durable, structured review results."""
    return Column(MutableList.as_mutable(JSONB), nullable=nullable)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(default_factory=_uuid, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    role: str = Field(default="user", index=True)
    is_active: bool = True
    created_at: datetime = Field(default_factory=_now)


class Story(SQLModel, table=True):
    __tablename__ = "stories"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    title: str | None = None
    status: str = Field(default="draft", index=True)
    current_version_id: str | None = Field(default=None, index=True)
    flagged: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_now)


class StoryVersion(SQLModel, table=True):
    __tablename__ = "story_versions"

    id: str = Field(default_factory=_uuid, primary_key=True)
    story_id: str = Field(foreign_key="stories.id", index=True)
    parent_version_id: str | None = Field(default=None, index=True)
    version_number: int = 1
    genre: str | None = None
    mood: str | None = None
    state_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())
    created_from_feedback_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_now)


class Job(SQLModel, table=True):
    """One row per stage attempt. Powers the progress stepper and the admin
    failed-run log."""

    __tablename__ = "jobs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    story_id: str = Field(index=True)
    version_id: str = Field(index=True)
    stage: str = Field(index=True)
    status: str = Field(default="pending", index=True)
    attempt: int = 1
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)


class MediaAsset(SQLModel, table=True):
    """Audio clips, scene images, and the final episode.

    `dedupe_key` is what makes every paid generation idempotent: the worker looks
    for an existing row before calling OpenAI, so a Celery redelivery cannot
    double-bill. It is a single non-null string rather than a composite of
    nullable columns because Postgres treats NULLs as distinct in unique indexes.
    """

    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("version_id", "dedupe_key", name="uq_media_version_key"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    version_id: str = Field(index=True)
    dedupe_key: str
    kind: str = Field(index=True)
    object_key: str
    content_type: str = "audio/mpeg"
    line_id: str | None = Field(default=None, index=True)
    scene_id: str | None = Field(default=None, index=True)
    duration_ms: int | None = None
    voice_used: str | None = None
    instructions_used: str | None = None
    size_bytes: int | None = None
    created_at: datetime = Field(default_factory=_now)


class Feedback(SQLModel, table=True):
    """One free-text note plus the outcome of interpreting it.

    `status` and `error` exist because interpretation can fail on a decision
    rather than an outage, and those failures roll the whole transaction back.
    Without a durable outcome the user's note would simply vanish.
    """

    __tablename__ = "feedback"

    id: str = Field(default_factory=_uuid, primary_key=True)
    version_id: str = Field(index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    raw_text: str
    status: str = Field(default="pending", index=True)
    error: str | None = None
    directive_json: dict[str, Any] | None = Field(
        default=None, sa_column=_json_column(nullable=True)
    )
    resulting_version_id: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=_now)


class ConsistencyCheck(SQLModel, table=True):
    """One read-only Plot Hole Hunter request against an immutable story version.

    Results live outside ``StoryVersion.state_json`` so running a quality check
    never changes the version that was checked. Keeping a short history also
    makes a retry/audit possible after the listener closes the studio.
    """

    __tablename__ = "consistency_checks"

    id: str = Field(default_factory=_uuid, primary_key=True)
    story_id: str = Field(foreign_key="stories.id", index=True)
    version_id: str = Field(foreign_key="story_versions.id", index=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    status: str = Field(default="pending", index=True)
    task_id: str | None = Field(default=None, index=True)
    # A worker lease identifier, never exposed to clients. A redelivered task
    # must atomically claim this before it can make a paid model call, and an
    # old lease may not overwrite the result of a newer retry.
    run_token: str | None = Field(default=None, index=True)
    # ``version_id`` while the check is pending/running, then NULL once it is
    # terminal.  The unique constraint turns a two-tab API race into one
    # durable active check without preventing an audit history of completed
    # reviews (Postgres and SQLite both permit multiple NULL values).
    active_key: str | None = Field(default=None, index=True)
    summary: str | None = None
    findings_json: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=_json_list_column()
    )
    # This is always a short, public-safe status message; worker exceptions are
    # logged privately and never persisted here verbatim.
    error: str | None = None
    created_at: datetime = Field(default_factory=_now, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    __table_args__ = (
        UniqueConstraint("version_id", "active_key", name="uq_consistency_checks_active_version"),
    )


class CostLedger(SQLModel, table=True):
    """One row per paid API call. Written from the gateway, never from a node."""

    __tablename__ = "cost_ledger"

    id: str = Field(default_factory=_uuid, primary_key=True)
    version_id: str | None = Field(default=None, index=True)
    user_id: str | None = Field(default=None, index=True)
    stage: str = Field(index=True)
    model: str = Field(index=True)
    input_tokens: int = 0
    output_tokens: int = 0
    unit_count: float = 0.0  # images generated, or seconds of audio
    cost_usd: float = 0.0
    # TTS cost cannot be read back from the API, so it is derived from duration.
    is_estimated: bool = False
    # A cache hit still writes a row, at zero cost. Dropping the row instead
    # would make spend simply disappear; this way the saving is measurable.
    cache_hit: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=_now, index=True)


class PipelineRun(SQLModel, table=True):
    __tablename__ = "pipeline_runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    version_id: str = Field(index=True)
    status: str = Field(default="running", index=True)
    langfuse_trace_id: str | None = None
    mlflow_run_id: str | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


class AdminSetting(SQLModel, table=True):
    """Runtime configuration the admin panel can change without a redeploy:
    per-stage model overrides, voice presets, feature flags, budget cap."""

    __tablename__ = "admin_settings"

    key: str = Field(primary_key=True)
    value_json: dict[str, Any] = Field(default_factory=dict, sa_column=_json_column())
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=_now)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: str = Field(default_factory=_uuid, primary_key=True)
    actor_user_id: str | None = Field(default=None, index=True)
    action: str = Field(index=True)
    target_type: str | None = None
    target_id: str | None = Field(default=None, index=True)
    metadata_json: dict[str, Any] | None = Field(
        default=None, sa_column=_json_column(nullable=True)
    )
    created_at: datetime = Field(default_factory=_now, index=True)


class IngestJob(SQLModel, table=True):
    """An uploaded document on its way to becoming story text.

    Deliberately not a `Story`: extraction can fail, OCR can return nonsense, and
    the cleaned text is shown to the user for review before anything is
    generated. A story row created here would have to be reaped on every one of
    those paths. The job ends by handing text to the compose form, and story
    creation stays exactly as it was.
    """

    __tablename__ = "ingest_jobs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    filename: str
    content_type: str
    size_bytes: int
    object_key: str
    status: str = Field(default="pending", index=True)
    method: str | None = None
    page_count: int | None = None
    raw_chars: int | None = None
    cleaned_text: str | None = None
    title_hint: str | None = None
    genre_hint: str | None = None
    notes: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_now, index=True)
    finished_at: datetime | None = None


class RateLimitEvent(SQLModel, table=True):
    """Counted over a rolling window. Lives in the database rather than memory so
    limits survive an API restart and hold across multiple API replicas."""

    __tablename__ = "rate_limit_events"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(index=True)
    action: str = Field(index=True)
    created_at: datetime = Field(default_factory=_now, index=True)
