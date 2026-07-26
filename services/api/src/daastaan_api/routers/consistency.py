"""On-demand Plot Hole Hunter endpoints.

These routes deliberately do not touch the pipeline run or story status. A
consistency check is a read-only editorial inspection of a completed version,
with a small durable result record that the studio can poll independently.
"""

import structlog
from daastaan_common.models import ConsistencyCheck, StoryVersion
from daastaan_contracts import (
    ConsistencyCheckStatus,
    ConsistencyFinding,
    StoryState,
    StoryStatus,
    limits,
)
from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..deps import CurrentUser, OwnedStory, SessionDep
from ..dispatch import dispatch_consistency_check
from ..guards import audit, enforce_budget, enforce_rate_limit
from ..schemas import ConsistencyCheckOut

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/stories", tags=["consistency"])

_DISPATCH_FAILURE = "Consistency check could not be queued. Please try again."
_CORRUPT_RESULT = "Consistency check result is unavailable. Please run it again."
_PUBLIC_FAILURE = "Consistency check could not be completed. Please try again."
_PUBLIC_RETRYING = "Consistency check is retrying."


def _display_text(value: str | None, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    printable = "".join(char for char in value if char.isprintable() or char.isspace())
    return " ".join(printable.split())[:limit].rstrip() or None


def _current_checkable_version(story, session: SessionDep) -> StoryVersion:  # type: ignore[no-untyped-def]
    """Return a finished version with enough generated structure to inspect."""
    if story.status != StoryStatus.READY:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "consistency checks are available when the episode is ready",
        )
    version = (
        session.get(StoryVersion, story.current_version_id)
        if story.current_version_id
        else None
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "story has no finished version yet")
    try:
        state = StoryState.model_validate(version.state_json)
    except (TypeError, ValueError) as exc:
        log.warning("consistency_version_state_invalid", version_id=version.id)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "story structure is not ready for a consistency check",
        ) from exc
    if not state.scenes or not state.lines:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "story structure is not ready for a consistency check",
        )
    return version


def _out(check: ConsistencyCheck) -> ConsistencyCheckOut:
    """Defensively project only validated findings out of the JSON result row."""
    findings: list[ConsistencyFinding] = []
    for raw in (check.findings_json or [])[:12]:
        try:
            finding = ConsistencyFinding.model_validate(raw)
        except ValidationError:
            # The worker only writes validated records, but never let a manual
            # DB edit or an old-format row turn into arbitrary client JSON.
            log.warning("consistency_result_entry_invalid", check_id=check.id)
            continue
        explanation = _display_text(finding.explanation, limit=600)
        suggestion = _display_text(finding.suggestion, limit=500)
        if explanation and suggestion:
            findings.append(
                finding.model_copy(
                    update={"explanation": explanation, "suggestion": suggestion}
                )
            )

    try:
        check_status = ConsistencyCheckStatus(check.status)
    except ValueError:
        check_status = ConsistencyCheckStatus.FAILED

    if check_status == ConsistencyCheckStatus.FAILED:
        error = (
            check.error
            if check.error in {_DISPATCH_FAILURE, _PUBLIC_FAILURE}
            else _CORRUPT_RESULT
        )
    elif check.error == _PUBLIC_RETRYING:
        error = _PUBLIC_RETRYING
    else:
        error = None

    return ConsistencyCheckOut(
        id=check.id,
        story_id=check.story_id,
        version_id=check.version_id,
        status=check_status,
        task_id=check.task_id,
        summary=(
            _display_text(check.summary, limit=500)
            if check_status == ConsistencyCheckStatus.SUCCEEDED
            else None
        ),
        findings=findings if check_status == ConsistencyCheckStatus.SUCCEEDED else [],
        error=error,
        created_at=check.created_at,
        started_at=check.started_at,
        finished_at=check.finished_at,
    )


@router.get("/{story_id}/consistency-checks", response_model=list[ConsistencyCheckOut])
def list_consistency_checks(story: OwnedStory, session: SessionDep) -> list[ConsistencyCheckOut]:
    """Return the recent reviews for the current version only.

    A result for an older branch must never be displayed as a review of the
    current ending, so version is part of the query rather than just the story.
    """
    version = _current_checkable_version(story, session)
    rows = session.exec(
        select(ConsistencyCheck)
        .where(ConsistencyCheck.version_id == version.id)
        .order_by(ConsistencyCheck.created_at.desc())
        .limit(10)
    ).all()
    return [_out(row) for row in rows]


@router.post(
    "/{story_id}/consistency-checks",
    response_model=ConsistencyCheckOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_consistency_check(
    story: OwnedStory, session: SessionDep, user: CurrentUser
) -> ConsistencyCheckOut:
    """Queue one idempotent-in-flight, lightweight continuity review."""
    version = _current_checkable_version(story, session)

    # Repeated clicks, a slow browser retry, and two tabs converge on the same
    # in-flight review instead of paying for concurrent duplicate model calls.
    existing = session.exec(
        select(ConsistencyCheck)
        .where(
            ConsistencyCheck.version_id == version.id,
            ConsistencyCheck.active_key == version.id,
        )
        .order_by(ConsistencyCheck.created_at.desc())
        .limit(1)
    ).first()
    if existing:
        return _out(existing)

    enforce_rate_limit(
        session,
        user.id,
        "consistency_check",
        limits.RATE_LIMIT_CONSISTENCY_CHECKS,
    )
    enforce_budget(session)

    check = ConsistencyCheck(
        story_id=story.id,
        version_id=version.id,
        user_id=user.id,
        status=ConsistencyCheckStatus.PENDING.value,
        active_key=version.id,
    )
    session.add(check)
    try:
        session.flush()
        audit(
            session,
            actor_user_id=user.id,
            action="story.consistency_check",
            target_type="story_version",
            target_id=version.id,
            metadata={"check_id": check.id},
        )
        session.commit()
    except IntegrityError:
        # The early read above keeps the common path cheap. The unique active
        # key is the authoritative race guard for two requests that both read
        # before either commits, so replay the lookup after rolling back.
        session.rollback()
        existing = session.exec(
            select(ConsistencyCheck)
            .where(
                ConsistencyCheck.version_id == version.id,
                ConsistencyCheck.active_key == version.id,
            )
            .order_by(ConsistencyCheck.created_at.desc())
            .limit(1)
        ).first()
        if existing:
            return _out(existing)
        raise

    try:
        check.task_id = dispatch_consistency_check(check_id=check.id, user_id=user.id)
        session.add(check)
        session.commit()
    except Exception:
        # A durable status is more useful than a silent forever-pending row;
        # keep the operational exception in logs and return only a safe message.
        log.exception("consistency_dispatch_failed", check_id=check.id)
        check.status = ConsistencyCheckStatus.FAILED.value
        check.error = _DISPATCH_FAILURE
        check.active_key = None
        session.add(check)
        session.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _DISPATCH_FAILURE) from None

    return _out(check)
