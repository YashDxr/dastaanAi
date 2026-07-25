"""Live progress, over SSE and WebSocket.

Workers append stage transitions, sub-stage counts and mid-call previews to a
per-story Redis stream, and both endpoints here forward them. SSE is what the web
client uses: it is a plain GET, so it carries the session cookie without the
query-string token dance a WebSocket handshake needs, and the browser reconnects on
its own.

Because the transport is a stream rather than pub/sub, a reconnect is no longer a
hole in the client's picture. `EventSource` remembers the last `id:` it saw and
replays it in `Last-Event-ID`, and a client arriving with no such header is sent the
whole retained log - which is what lets a page reload mid-run rebuild previews that
exist nowhere else.

Both endpoints remain an enhancement rather than a requirement. `GET /api/stories/{id}/jobs`
reads the same stage transitions back out of the database, so a Redis outage or a
blocked stream degrades the client to polling instead of breaking it.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

import structlog
from daastaan_common import events, get_settings, session_scope
from daastaan_common.models import Story
from daastaan_contracts import HeartbeatEvent, UserRole, progress_event_adapter
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse

from ..security import decode_access_token

log = structlog.get_logger(__name__)

# Two routers because they mount differently: the WebSocket route carries its own
# /ws prefix and is mounted at the root, while SSE is an ordinary GET and belongs
# under /api with everything else the client fetches.
router = APIRouter(tags=["progress"])
sse_router = APIRouter(tags=["progress"])

WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403

# Long enough not to be chatty, short enough that a proxy with a 30s idle
# timeout never sees silence during the multi-minute TTS fan-out.
_IDLE_TIMEOUT_SECONDS = 15.0

# A Redis stream ID: millisecond timestamp, dash, sequence. This value arrives in a
# client-supplied header and is interpolated into a Redis command, so it is matched
# against the exact shape rather than trusted - a malformed one is treated as a
# fresh connection instead of being passed through.
_STREAM_ID = re.compile(r"\A\d{1,20}-\d{1,20}\Z")


def _resume_from(raw: str | None) -> str | None:
    """The stream ID a reconnecting client wants to resume after.

    `None` means start from the beginning of the retained log, which is the right
    default for a first connection: the client gets the run so far rather than only
    whatever happens next.
    """
    if not raw or not _STREAM_ID.match(raw):
        return None
    return raw


def _authorize(token: str | None, story_id: str) -> bool:
    if not token:
        return False
    try:
        payload = decode_access_token(token)
    except Exception:
        return False

    with session_scope() as session:
        story = session.get(Story, story_id)
        if story is None:
            return False
        return story.user_id == payload.get("sub") or payload.get("role") == UserRole.ADMIN


async def _event_stream(story_id: str, request: Request, last_id: str | None) -> AsyncIterator[str]:
    """A story's event stream rendered as an SSE body.

    Hand-rolled rather than pulling in an SSE library: the wire format is a couple
    of lines per event, and owning it keeps the `id:` bookkeeping, the comment-ping
    and the disconnect handling explicit.
    """
    reader = events.read_events(
        story_id, last_id=last_id, idle_timeout_seconds=_IDLE_TIMEOUT_SECONDS
    )
    try:
        # Tells EventSource how long to wait before reconnecting, and gives the
        # client something to see immediately so it can treat the stream as live.
        yield "retry: 3000\n\n"
        # `aclosing` rather than a bare `async for`: leaving the loop early only
        # schedules the generator's cleanup for whenever it is collected, so every
        # client that navigated away would hold its Redis connection open until then.
        async with aclosing(reader):
            async for item in reader:
                # A client that navigated away leaves the read suspended on the next
                # event; without this the connection would linger until the next
                # publish happened to wake it.
                if await request.is_disconnected():
                    break
                if item is None:
                    # A comment line: valid SSE, ignored by EventSource, and enough
                    # to stop an intermediary treating the stream as idle.
                    yield ": ping\n\n"
                    continue
                # The `id:` is what the browser echoes back in `Last-Event-ID`, so
                # emitting it is the entire cost of gapless reconnection.
                yield f"id: {item.event_id}\ndata: {item.data}\n\n"
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("progress_sse_failed", story_id=story_id)


# The name the event union is published under in `components/schemas`, and what the
# generated frontend types key off.
EVENT_SCHEMA_NAME = "ProgressEvent"

_EVENT_SCHEMA_REF = f"#/components/schemas/{EVENT_SCHEMA_NAME}"


def event_component_schemas() -> dict[str, Any]:
    """The event union and everything it references, as `components/schemas` entries.

    Published so the browser's event types stay generated from Pydantic rather than
    hand-maintained. A streaming body is outside what FastAPI can infer a
    `response_model` from, so `main.py` merges these in when it builds the document.

    They go in `components` rather than being inlined as `$defs` because the type
    generator resolves `#/components/schemas/...` and silently gives up on
    `#/$defs/...`, which produced an event type of `unknown`.
    """
    schema = progress_event_adapter.json_schema(ref_template="#/components/schemas/{model}")
    referenced = schema.pop("$defs", {})
    return {**referenced, EVENT_SCHEMA_NAME: schema}


@sse_router.get(
    "/stories/{story_id}/events",
    openapi_extra={
        "responses": {
            "200": {
                "description": "A stream of progress events, one per SSE frame.",
                "content": {"text/event-stream": {"schema": {"$ref": _EVENT_SCHEMA_REF}}},
            }
        }
    },
)
async def story_events(story_id: str, request: Request) -> StreamingResponse:
    """Server-sent progress events.

    `EventSource` cannot set headers, but it does send cookies for a same-origin
    request, and the web client reaches the API through the Vite proxy - so the
    normal session cookie authenticates this exactly like any other GET.
    """
    settings = get_settings()
    token = request.cookies.get(settings.cookie_name) or request.query_params.get("token")
    if not await asyncio.to_thread(_authorize, token, story_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authorized")

    return StreamingResponse(
        _event_stream(story_id, request, _resume_from(request.headers.get("last-event-id"))),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # nginx and friends will otherwise buffer the whole response and
            # deliver nothing until the stream closes, which defeats the point.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.websocket("/ws/stories/{story_id}")
async def story_progress(websocket: WebSocket, story_id: str) -> None:
    settings = get_settings()
    await websocket.accept()

    # Browsers cannot set headers on a WebSocket handshake, so the session cookie
    # is the primary credential; the query parameter covers non-browser clients.
    token = websocket.cookies.get(settings.cookie_name) or websocket.query_params.get("token")
    if not await asyncio.to_thread(_authorize, token, story_id):
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="not authorized")
        return

    # No `Last-Event-ID` equivalent on a socket handshake, so a reconnecting client
    # passes the last ID it applied as a query parameter instead.
    last_id = _resume_from(websocket.query_params.get("last_event_id"))
    heartbeat = HeartbeatEvent().model_dump_json()

    reader = events.read_events(
        story_id, last_id=last_id, idle_timeout_seconds=_IDLE_TIMEOUT_SECONDS
    )
    try:
        # See `_event_stream`: the disconnect arrives as an exception out of `send_text`,
        # so without this the reader's Redis connection outlives the socket.
        async with aclosing(reader):
            async for item in reader:
                if item is None:
                    # Keeps intermediaries from dropping an idle connection during
                    # the long TTS fan-out. A socket has no comment syntax, so unlike
                    # SSE this has to be a real message the client is expected to ignore.
                    await websocket.send_text(heartbeat)
                    continue
                await websocket.send_text(item.data)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("progress_ws_failed", story_id=story_id)
