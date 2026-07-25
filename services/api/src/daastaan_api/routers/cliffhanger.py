"""Cliffhanger Optimizer endpoints.

POST to start a new analysis, GET to list/poll results.
"""

from datetime import UTC, datetime

import structlog
from daastaan_common.models import CliffhangerAnalysis, StoryVersion
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select

from ..deps import CurrentUser, OwnedStory, SessionDep
from ..dispatch import dispatch_cliffhanger

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/stories", tags=["cliffhanger"])


class CliffhangerOut(BaseModel):
    id: str
    story_id: str
    version_id: str
    status: str
    result: dict | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


def _current_version(story, session) -> StoryVersion:  # type: ignore[no-untyped-def]
    version = (
        session.get(StoryVersion, story.current_version_id)
        if story.current_version_id
        else None
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "story has no finished version yet")
    return version


@router.post(
    "/{story_id}/cliffhanger",
    response_model=CliffhangerOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_cliffhanger(
    story: OwnedStory, session: SessionDep, user: CurrentUser
) -> CliffhangerOut:
    version = _current_version(story, session)

    row = CliffhangerAnalysis(
        story_id=story.id,
        version_id=version.id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    dispatch_cliffhanger(
        version_id=version.id, user_id=user.id, session_id=row.id
    )

    return CliffhangerOut(
        id=row.id,
        story_id=row.story_id,
        version_id=row.version_id,
        status=row.status,
        result=row.result_json,
        error=row.error,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


@router.get(
    "/{story_id}/cliffhanger",
    response_model=list[CliffhangerOut],
)
def list_cliffhanger(story: OwnedStory, session: SessionDep) -> list[CliffhangerOut]:
    rows = session.exec(
        select(CliffhangerAnalysis)
        .where(CliffhangerAnalysis.story_id == story.id)
        .order_by(CliffhangerAnalysis.created_at.desc())
    ).all()

    return [
        CliffhangerOut(
            id=r.id,
            story_id=r.story_id,
            version_id=r.version_id,
            status=r.status,
            result=r.result_json,
            error=r.error,
            created_at=r.created_at,
            finished_at=r.finished_at,
        )
        for r in rows
    ]
