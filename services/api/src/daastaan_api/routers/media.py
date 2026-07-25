from io import BytesIO

from daastaan_common import get_store
from daastaan_common.models import MediaAsset, Story, StoryVersion
from daastaan_contracts import UserRole
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from ..deps import CurrentUser, SessionDep

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/{asset_id}")
def stream_asset(asset_id: str, session: SessionDep, user: CurrentUser) -> StreamingResponse:
    """Serve audio and images through the API rather than handing out storage
    URLs, so ownership is re-checked on every fetch and artifacts are never
    publicly readable."""
    asset = session.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")

    version = session.get(StoryVersion, asset.version_id)
    story = session.get(Story, version.story_id) if version else None
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    if story.user_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")

    try:
        data = get_store().get(asset.object_key)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset bytes missing") from exc

    return StreamingResponse(
        BytesIO(data),
        media_type=asset.content_type,
        headers={
            "Content-Length": str(len(data)),
            "Cache-Control": "private, max-age=3600",
            "Accept-Ranges": "none",
        },
    )
