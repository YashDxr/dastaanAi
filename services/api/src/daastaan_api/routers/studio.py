"""Presentation adapters for studio screens.

These routes deliberately reuse existing tables and return transient settings or
voice previews. They add no SQLModel definitions, migrations, or database writes.
"""

from typing import Any

from daastaan_common.models import Job, Story
from fastapi import APIRouter
from sqlmodel import select

from ..deps import CurrentUser, SessionDep

router = APIRouter(tags=["studio"])


@router.get("/observability/jobs")
def observability_jobs(session: SessionDep, user: CurrentUser) -> dict[str, Any]:
    story_ids = list(session.exec(select(Story.id).where(Story.user_id == user.id)).all())
    jobs = (
        list(
            session.exec(
                select(Job).where(Job.story_id.in_(story_ids)).order_by(Job.created_at.desc())
            ).all()
        )
        if story_ids
        else []
    )
    failed = sum(job.status == "failed" for job in jobs)
    return {
        "active_jobs": sum(job.status in {"pending", "running"} for job in jobs),
        "failure_rate": round((failed / len(jobs)) * 100, 2) if jobs else 0,
        "jobs": [
            {"story_id": job.story_id, "stage": job.stage, "status": job.status,
             "attempt": job.attempt, "error": job.error, "created_at": job.created_at}
            for job in jobs
        ],
    }


_DEFAULT_SETTINGS = {
    "narrator": "romance",
    "language": "English",
    "audio_quality": "Studio · 48kHz",
    "notifications": True,
    "reduced_motion": False,
}


@router.get("/settings")
def get_settings(_: CurrentUser) -> dict[str, Any]:
    """No user-preferences table exists yet, so this is a non-persistent default."""
    return _DEFAULT_SETTINGS


@router.patch("/settings")
def preview_settings(body: dict[str, Any], _: CurrentUser) -> dict[str, Any]:
    """Echo settings for a live preview; intentionally does not write to the DB."""
    return {**_DEFAULT_SETTINGS, **body, "persisted": False}
