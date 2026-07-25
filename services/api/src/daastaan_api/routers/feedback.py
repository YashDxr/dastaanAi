from daastaan_common.models import Feedback, StoryVersion
from daastaan_contracts import limits
from fastapi import APIRouter, HTTPException, status

from ..deps import CurrentUser, OwnedStory, SessionDep
from ..dispatch import dispatch_feedback_interpretation
from ..guards import audit, enforce_budget, enforce_rate_limit
from ..schemas import FeedbackRequest

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

    feedback = Feedback(
        version_id=story.current_version_id,
        user_id=user.id,
        raw_text=body.raw_text,
    )
    session.add(feedback)
    session.flush()

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
