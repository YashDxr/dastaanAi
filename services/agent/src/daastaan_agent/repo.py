"""State persistence and progress reporting.

Tasks carry a `version_id` and rehydrate `StoryState` from the database here,
rather than serialising the whole state through Redis. Queue messages stay small,
and any task can be retried in isolation because it reads current state instead of
whatever was true when it was enqueued.
"""

import json
from datetime import UTC, datetime
from typing import Any

import redis
import structlog
from daastaan_common import get_settings
from daastaan_common.models import Job, MediaAsset, Story, StoryVersion
from daastaan_contracts import (
    FANOUT_STAGES,
    AssetKind,
    JobStatus,
    StageName,
    StoryState,
    progress_channel,
)
from sqlmodel import Session, col, select

log = structlog.get_logger(__name__)

_redis: redis.Redis | None = None


def _client() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def load_state(session: Session, version_id: str) -> StoryState:
    version = session.get(StoryVersion, version_id)
    if version is None:
        raise LookupError(f"story version {version_id} not found")
    return StoryState.model_validate(version.state_json)


def save_state(session: Session, state: StoryState) -> None:
    version = session.get(StoryVersion, state.version_id)
    if version is None:
        raise LookupError(f"story version {state.version_id} not found")

    version.state_json = state.model_dump(mode="json")
    if state.mood:
        version.genre, version.mood = state.mood.genre, state.mood.mood
    if state.title:
        story = session.get(Story, state.story_id)
        if story and not story.title:
            story.title = state.title
    session.add(version)
    session.commit()


def publish(story_id: str, event: dict[str, Any]) -> None:
    """Best-effort progress push. The `jobs` table is the durable record, so a
    Redis hiccup degrades the UI to polling instead of losing the update."""
    try:
        _client().publish(progress_channel(story_id), json.dumps(event))
    except Exception:
        log.warning("progress_publish_failed", story_id=story_id, exc_info=True)


def start_job(session: Session, *, story_id: str, version_id: str, stage: StageName) -> Job:
    existing = session.exec(
        select(Job).where(Job.version_id == version_id, Job.stage == stage.value)
    ).first()

    job = existing or Job(story_id=story_id, version_id=version_id, stage=stage.value)
    job.attempt = job.attempt + 1 if existing else 1
    job.status = JobStatus.RUNNING
    job.started_at = datetime.now(UTC)
    job.error = None
    session.add(job)
    session.commit()

    publish(story_id, {"type": "stage", "stage": stage.value, "status": JobStatus.RUNNING.value})
    return job


def finish_job(
    session: Session, job: Job, *, status: JobStatus, error: str | None = None
) -> None:
    job.status = status
    job.error = error[:2000] if error else None
    job.finished_at = datetime.now(UTC)
    session.add(job)
    session.commit()

    publish(
        job.story_id,
        {"type": "stage", "stage": job.stage, "status": status.value, "error": job.error},
    )


def close_fanout_jobs(
    session: Session, version_id: str, *, status: JobStatus = JobStatus.SUCCEEDED
) -> None:
    """Settle the TTS and image rows opened by `fan_out`.

    Those two stages are groups of subtasks rather than a single task, so nothing
    inside them owns the stage row. `fan_out` opens the rows and the chord callback
    closes them here - which is accurate, because the callback only runs once every
    member of the group has finished.
    """
    jobs = session.exec(
        select(Job).where(
            Job.version_id == version_id,
            col(Job.stage).in_([stage.value for stage in FANOUT_STAGES]),
        )
    ).all()
    for job in jobs:
        if job.status == JobStatus.RUNNING:
            finish_job(session, job, status=status)


def find_asset(
    session: Session, *, version_id: str, dedupe_key: str
) -> MediaAsset | None:
    """The idempotency guard. Called before every paid generation so a Celery
    redelivery re-uses the existing artifact instead of paying twice."""
    return session.exec(
        select(MediaAsset).where(
            MediaAsset.version_id == version_id, MediaAsset.dedupe_key == dedupe_key
        )
    ).first()


def record_asset(
    session: Session,
    *,
    version_id: str,
    kind: AssetKind,
    dedupe_key: str,
    object_key: str,
    content_type: str,
    line_id: str | None = None,
    scene_id: str | None = None,
    duration_ms: int | None = None,
    voice_used: str | None = None,
    instructions_used: str | None = None,
    size_bytes: int | None = None,
) -> MediaAsset:
    asset = find_asset(session, version_id=version_id, dedupe_key=dedupe_key)
    if asset is None:
        asset = MediaAsset(version_id=version_id, dedupe_key=dedupe_key, kind=kind.value)
        session.add(asset)

    asset.object_key = object_key
    asset.content_type = content_type
    asset.line_id = line_id
    asset.scene_id = scene_id
    asset.duration_ms = duration_ms
    asset.voice_used = voice_used
    asset.instructions_used = instructions_used
    asset.size_bytes = size_bytes
    session.commit()
    session.refresh(asset)
    return asset
