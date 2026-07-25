"""Live progress, over SSE and WebSocket.

Workers publish stage transitions to Redis pub/sub and both endpoints here
forward them. SSE is what the web client uses: it is a plain GET, so it carries
the session cookie without the query-string token dance a WebSocket handshake
needs, and the browser reconnects on its own.

Both remain an enhancement rather than a requirement. `GET /api/stories/{id}/jobs`
reads the same transitions back out of the database, so a flaky network degrades
the client to polling instead of breaking it.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
import structlog
from daastaan_common import get_settings, session_scope
from daastaan_common.models import Story
from daastaan_contracts import UserRole, progress_channel
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


async def _event_stream(story_id: str, request: Request) -> AsyncIterator[str]:
    """Redis pub/sub rendered as an SSE body.

    Hand-rolled rather than pulling in an SSE library: the wire format is two
    lines per event, and owning it keeps the comment-ping and disconnect handling
    explicit.
    """
    settings = get_settings()
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(progress_channel(story_id))

    try:
        # Tells EventSource how long to wait before reconnecting, and gives the
        # client something to see immediately so it can treat the stream as live.
        yield "retry: 3000\n\n"
        while True:
            # A client that navigated away leaves the generator suspended on the
            # next read; without this the subscription would leak until the
            # worker published again.
            if await request.is_disconnected():
                break
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_IDLE_TIMEOUT_SECONDS
            )
            if message is None:
                # A comment line: valid SSE, ignored by EventSource, and enough
                # to stop an intermediary treating the stream as idle.
                yield ": ping\n\n"
                continue
            yield f"data: {message['data']}\n\n"
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("progress_sse_failed", story_id=story_id)
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(progress_channel(story_id))
            await pubsub.aclose()
            await client.aclose()


@sse_router.get("/stories/{story_id}/events")
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
        _event_stream(story_id, request),
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

    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(progress_channel(story_id))

    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
            if message is None:
                # Keeps intermediaries from dropping an idle connection during
                # the long TTS fan-out.
                await websocket.send_json({"type": "heartbeat"})
                continue
            await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("progress_ws_failed", story_id=story_id)
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(progress_channel(story_id))
            await pubsub.aclose()
            await client.aclose()
