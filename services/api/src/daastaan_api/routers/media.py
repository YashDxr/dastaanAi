import re
from typing import Annotated

from daastaan_common import get_store
from daastaan_common.models import MediaAsset, Story, StoryVersion
from daastaan_contracts import UserRole
from fastapi import APIRouter, Header, HTTPException, Response, status

from ..deps import CurrentUser, SessionDep

router = APIRouter(prefix="/media", tags=["media"])

# Only the single-range form is honoured. Multi-range replies need a multipart
# body that no browser media element asks for.
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

# Cap on how much a single 206 may return. Without it a client asking for
# `bytes=0-` would pull the whole episode in one response, which defeats the
# point of ranged delivery on a slow connection.
_MAX_CHUNK_BYTES = 2 * 1024 * 1024


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """Resolve a Range header to an inclusive `(start, end)` byte pair.

    Returns None when the header is syntactically unusable, which callers treat
    as "ignore the header and send the whole body" per RFC 9110. Ranges that
    parse but fall outside the object are a different case and raise 416.
    """
    match = _RANGE_RE.match(header.strip())
    if not match or size == 0:
        return None

    raw_start, raw_end = match.group(1), match.group(2)
    if not raw_start and not raw_end:
        return None

    if not raw_start:
        # `bytes=-500` means the trailing 500 bytes.
        length = int(raw_end)
        if length == 0:
            raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "empty range")
        start, end = max(0, size - length), size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1

    if start >= size or start > end:
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "range out of bounds")

    end = min(end, size - 1, start + _MAX_CHUNK_BYTES - 1)
    return start, end


@router.get("/{asset_id}")
def stream_asset(
    asset_id: str,
    session: SessionDep,
    user: CurrentUser,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    """Serve audio and images through the API rather than handing out storage
    URLs, so ownership is re-checked on every fetch and artifacts are never
    publicly readable.

    Range support is not optional for the audio player: an element that cannot
    issue byte ranges has to refetch from zero to move the playhead, which is
    indistinguishable from restarting the track.
    """
    asset = session.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")

    version = session.get(StoryVersion, asset.version_id)
    story = session.get(Story, version.story_id) if version else None
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    if story.user_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")

    # The store exposes whole objects only, so a ranged reply still reads the
    # full artifact and slices it. Episodes are single-digit megabytes and the
    # local backend serves them from page cache, so a partial-read API is not
    # worth adding to both storage backends yet.
    try:
        data = get_store().get(asset.object_key)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset bytes missing") from exc

    size = len(data)
    headers = {
        "Accept-Ranges": "bytes",
        # Immutable: a regenerated asset is written under a new id, so a stale
        # cache entry can never shadow a newer mix.
        "Cache-Control": "private, max-age=3600, immutable",
        "ETag": f'"{asset.id}"',
    }

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
