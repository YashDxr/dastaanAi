"""Shared FastAPI dependencies: session, identity, authorisation.

Authorisation is enforced here rather than in the UI. `require_admin` returns 403
server-side, so the admin panel being a separate React app that simply is not
linked from the user app is a convenience, not a control.
"""

from typing import Annotated

from daastaan_common import get_session, get_settings
from daastaan_common.models import Story, User
from daastaan_contracts import UserRole
from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session

from .security import decode_access_token

SessionDep = Annotated[Session, Depends(get_session)]


def _read_token(request: Request) -> str | None:
    """Cookie first (the browser apps), bearer header second (scripts, and a
    future mobile client that cannot use cookies)."""
    settings = get_settings()
    if token := request.cookies.get(settings.cookie_name):
        return token
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:]
    return None


def current_user(request: Request, session: SessionDep) -> User:
    token = _read_token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    try:
        payload = decode_access_token(token)
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired session") from exc

    user = session.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user no longer active")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def owned_story(story_id: str, session: SessionDep, user: CurrentUser) -> Story:
    """Load a story the caller is allowed to act on.

    Returns 404 rather than 403 when another user owns it, so the endpoint does
    not confirm that someone else's story id exists.
    """
    story = session.get(Story, story_id)
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "story not found")
    if story.user_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "story not found")
    return story


OwnedStory = Annotated[Story, Depends(owned_story)]
