from daastaan_common.models import Feedback, StoryVersion
from daastaan_contracts import FeedbackStatus, StoryStatus, limits
from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from ..deps import CurrentUser, OwnedStory, SessionDep
from ..dispatch import dispatch_feedback_interpretation
from ..guards import audit, enforce_budget, enforce_rate_limit
from ..schemas import FeedbackOut, FeedbackRequest

router = APIRouter(prefix="/stories", tags=["feedback"])


@router.post("/{story_id}/feedback", status_code=status.HTTP_202_ACCEPTED)
def submit_feedback(
    body: FeedbackRequest, story: OwnedStory, session: SessionDep, user: CurrentUser
) -> dict[str, str]:
    """Free-text feedback.

    The text is only stored here. Interpreting it into a regeneration directive
    happens in the agent service, and the resulting directive is re-validated
    against the stage registry before any work is dispatched - the API never
    trusts the model to choose what runs.
    """
    enforce_rate_limit(session, user.id, "regenerate", limits.RATE_LIMIT_REGENERATIONS)
    enforce_budget(session)

    if not story.current_version_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "story has no generated version yet")
    if session.get(StoryVersion, story.current_version_id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "current version missing")
    if story.status != StoryStatus.READY:
        raise HTTPException(status.HTTP_409_CONFLICT, "story regeneration is already in progress")

    feedback = Feedback(
        version_id=story.current_version_id,
        user_id=user.id,
        raw_text=body.raw_text,
        status=FeedbackStatus.PENDING,
    )
    session.add(feedback)
    session.flush()

    # Marked generating here rather than in the worker. The client refreshes as
    # soon as this 202 lands, and if the story still read `ready` at that moment
    # it would never start watching progress - the interpretation would run to
    # completion behind a UI that thought nothing was happening.
    story.status = StoryStatus.GENERATING

    audit(session, actor_user_id=user.id, action="story.feedback", target_type="feedback",
          target_id=feedback.id)
    session.commit()

    task_id = dispatch_feedback_interpretation(
        story_id=story.id,
        version_id=story.current_version_id,
        user_id=user.id,
        feedback_id=feedback.id,
    )
    return {"feedback_id": feedback.id, "task_id": task_id}


@router.get("/{story_id}/feedback", response_model=list[FeedbackOut])
def list_feedback(story: OwnedStory, session: SessionDep) -> list[FeedbackOut]:
    """Revision history for the story.

    Carries the interpreted directive and any failure reason, so the studio can
    tell the user what their note was understood to mean instead of leaving them
    guessing whether it landed.
    """
    versions = session.exec(
        select(StoryVersion.id).where(StoryVersion.story_id == story.id)
    ).all()
    if not versions:
        return []

    rows = session.exec(
        select(Feedback)
        .where(Feedback.version_id.in_(versions))  # type: ignore[attr-defined]
        .order_by(Feedback.created_at.desc())  # type: ignore[attr-defined]
        .limit(20)
    ).all()
    return [FeedbackOut.model_validate(row, from_attributes=True) for row in rows]
