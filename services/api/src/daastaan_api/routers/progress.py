"""Live progress over WebSocket.

Workers publish stage transitions to Redis pub/sub and this endpoint forwards
them. It is deliberately an enhancement: `GET /api/stories/{id}/jobs` reads the
same transitions from the database, so a flaky network at the venue degrades the
demo to polling instead of breaking it.
"""

import asyncio
import contextlib

import redis.asyncio as aioredis
import structlog
from daastaan_common import get_settings, session_scope
from daastaan_common.models import Story
from daastaan_contracts import UserRole, progress_channel
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..security import decode_access_token

log = structlog.get_logger(__name__)
router = APIRouter(tags=["progress"])

WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403


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
