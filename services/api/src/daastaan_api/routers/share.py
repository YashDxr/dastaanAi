"""Public playback of a shared cut.

The only unauthenticated routes in the API. A share token is the whole
credential, so these are written to give up as little as possible: the token
resolves to exactly one cut, the response describes that clip and nothing about
the account behind it, and the bytes served are the ones the cut's own render
asset points at rather than any asset id a caller could name.

Revocation clears the token instead of dating it, so a withdrawn link stops
resolving rather than becoming a link with a date in the past. Cache lifetimes are
short and private for the same reason: a revoked link should stop working in
minutes, not when a CDN feels like it.
"""

from datetime import UTC, datetime
from typing import Annotated

import structlog
from daastaan_common import get_store
from daastaan_common.models import MediaAsset, Story, VideoEdit, as_utc
from daastaan_contracts import VideoEditManifest
from fastapi import APIRouter, Header, HTTPException, Response, status
from sqlmodel import Session, select

from ..deps import SessionDep
from ..schemas import SharedCutOut
from .media import content_disposition, download_filename, parse_range

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/share", tags=["share"])

# A shared clip is a link someone sent, not a page that should turn up in search.
_NO_INDEX = {"X-Robots-Tag": "noindex, nofollow"}

# Short and private. The link is revocable, so anything longer or shared would
# keep serving a cut after its owner withdrew it.
_CACHE_CONTROL = "private, max-age=60"


def _resolve(session: Session, token: str) -> tuple[VideoEdit, MediaAsset, Story]:
    """The cut a token names, or 404.

    Every failure is the same 404 - unknown token, expired token, cut deleted,
    render missing - because distinguishing them would turn this route into an
    oracle for which tokens exist.
    """
    # Tokens are `secrets.token_urlsafe(32)`, so anything far from that length is
    # not worth a query.
    if not token or len(token) > 128:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this link is not available")

    edit = session.exec(select(VideoEdit).where(VideoEdit.share_token == token)).first()
    if edit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this link is not available")
    if edit.share_expires_at is not None and as_utc(edit.share_expires_at) <= datetime.now(UTC):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this link has expired")

    asset = session.get(MediaAsset, edit.render_asset_id) if edit.render_asset_id else None
    story = session.get(Story, edit.story_id)
    if asset is None or not asset.object_key or story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this link is not available")
    return edit, asset, story


@router.get("/{token}", response_model=SharedCutOut)
def get_shared_cut(token: str, session: SessionDep, response: Response) -> SharedCutOut:
    """What a recipient needs to render the player page.

    The view counter is incremented here rather than on the video route: a media
    element issues many range requests for one viewing, and counting those would
    measure buffering rather than interest.
    """
    edit, asset, story = _resolve(session, token)
    manifest = VideoEditManifest.model_validate(edit.manifest_json)

    edit.share_views += 1
    session.add(edit)
    session.commit()

    response.headers.update(_NO_INDEX)
    response.headers["Cache-Control"] = "no-store"
    return SharedCutOut(
        title=story.title,
        name=edit.name,
        duration_ms=asset.duration_ms,
        aspect=manifest.aspect.value,
        video_url=f"/api/share/{token}/video",
        expires_at=as_utc(edit.share_expires_at) if edit.share_expires_at else None,
    )


@router.get("/{token}/video")
def stream_shared_cut(
    token: str,
    session: SessionDep,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    download: bool = False,
) -> Response:
    """Serve the cut's bytes.

    Range support is what makes the clip seekable; without it the player has to
    refetch from zero to move the playhead. The same whole-object read and slice
    as `/api/media`, for the same reason: the storage backends expose objects, not
    byte ranges.
    """
    _, asset, story = _resolve(session, token)

    try:
        data = get_store().get(asset.object_key)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this link is not available") from exc

    size = len(data)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": _CACHE_CONTROL,
        "ETag": f'"{asset.id}"',
        **_NO_INDEX,
    }
    if download:
        headers["Content-Disposition"] = content_disposition(
            download_filename(story.title, asset.object_key)
        )

    span = parse_range(range_header, size) if range_header else None
    if span is None:
        headers["Content-Length"] = str(size)
        return Response(content=data, media_type=asset.content_type, headers=headers)

    start, end = span
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return Response(
        content=data[start : end + 1],
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=asset.content_type,
        headers=headers,
    )
