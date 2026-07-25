"""The progress event bus: a capped Redis stream per story.

This replaced Redis pub/sub. Pub/sub has no memory, and that was the whole problem
with it here: a browser that lost its connection for four seconds - a laptop lid, a
tunnel, a Vite dev-server restart - permanently missed every event published in that
window. The client compensated by discarding the payloads it did receive and
re-reading `/jobs` after each one, which is why progress arrived in coarse jumps even
though events were flowing the whole time.

A stream fixes the gap at the source. Entries are retained under ordered IDs, so:

* a reconnecting client asks for everything after the last ID it saw. `EventSource`
  tracks that ID itself and replays it in `Last-Event-ID`, so ordered resumption
  costs the client nothing;
* a *fresh* client replays the whole retained log and rebuilds the run from it. That
  is what makes a page reload mid-generation show the characters and scene titles
  already discovered - they only ever existed as events, never as REST state.

Because the log is replayed in order and only ever trimmed from the front, a client
can treat REST state as a base and the event log as an overlay that wins wherever the
two overlap: the overlay is always the newer of the two. That is the property that
removes any need for sequence numbers or gap detection here.

Retention is bounded two ways, because this is progress telemetry and not a durable
log - the `jobs` table stays the source of truth for anything outliving a page view:

* `MAX_STREAM_LENGTH` caps a single busy story, so one pathological run cannot grow
  without limit;
* `STREAM_TTL_SECONDS` expires the key after the last publish, so finished stories
  clear themselves out without a reaper.

Nothing here may raise into a caller. A publish is a best-effort push, and Redis
being down has to degrade the UI to polling rather than fail the pipeline that was
trying to report progress.
"""

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import redis
import redis.asyncio as aioredis
import structlog
from daastaan_contracts import StageName
from daastaan_contracts.events import ProgressEvent, progress_event_adapter

from .settings import get_settings

log = structlog.get_logger(__name__)

_PREFIX = "daastaan:events"

# Roughly a whole chatty run: a 40-line story publishes a few hundred events once
# previews are counted. Far more than a reconnecting client needs, and small enough
# that the memory cost is irrelevant.
MAX_STREAM_LENGTH = 2000

# Long enough to survive a meaningful outage and a page reload well after the run
# finished; short enough that nothing accumulates indefinitely.
STREAM_TTL_SECONDS = 6 * 60 * 60

# Streams are field/value maps. The whole event is kept as one JSON blob so the wire
# format stays owned by `contracts` rather than being half-expressed as field names.
_FIELD = "data"

# Fields of the per-stage fan-out hash.
_TOTAL = "total"
_DONE = "done"

# Exclusive lower bound meaning "everything retained". Stream IDs are
# `<ms>-<seq>` and no real entry can sort at or below this one.
ORIGIN = "0-0"

_sync_client: redis.Redis | None = None


def _client() -> redis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _sync_client


def stream_key(story_id: str) -> str:
    return f"{_PREFIX}:{story_id}"


def _fanout_key(version_id: str, stage: StageName) -> str:
    return f"{_PREFIX}:fanout:{version_id}:{stage.value}"


def publish(story_id: str, event: ProgressEvent) -> None:
    """Append one event to a story's stream.

    Best-effort by contract: the pipeline must keep running when Redis does not.
    """
    try:
        payload = progress_event_adapter.dump_json(event).decode()
        key = stream_key(story_id)
        # One round trip. `approximate` lets Redis trim on whole-node boundaries,
        # which is the cheap variant and the reason capping costs nothing here.
        pipe = _client().pipeline(transaction=False)
        pipe.xadd(key, {_FIELD: payload}, maxlen=MAX_STREAM_LENGTH, approximate=True)
        pipe.expire(key, STREAM_TTL_SECONDS)
        pipe.execute()
    except Exception:
        log.warning("progress_publish_failed", story_id=story_id, exc_info=True)


def open_fanout(version_id: str, stage: StageName, *, total: int, completed: int = 0) -> None:
    """Record how much work a fan-out stage covers, and how much is already done.

    The total is stored here rather than passed to each subtask because the
    subtasks are already-enqueued Celery messages with a fixed signature; keeping
    it beside the counter meant per-line progress needed no change to the task
    contract.

    `total` counts everything the version needs and `completed` the part carried
    over from a parent version, rather than either being scoped to the subtasks this
    run enqueued. That is what keeps the live number and the one `GET /stories/{id}/jobs`
    recomputes from `media_assets` in agreement: a regeneration that respeaks one
    line of a 40-line story reports "39 of 40" moving to "40 of 40", and not "0 of 1"
    on the stream while REST insists on "40 of 40".

    Overwrites any previous run's numbers, so re-entering a stage does not continue
    the parent version's tally.
    """
    try:
        key = _fanout_key(version_id, stage)
        pipe = _client().pipeline(transaction=False)
        pipe.delete(key)
        pipe.hset(key, mapping={_TOTAL: total, _DONE: completed})
        pipe.expire(key, STREAM_TTL_SECONDS)
        pipe.execute()
    except Exception:
        log.warning("fanout_open_failed", version_id=version_id, stage=stage.value, exc_info=True)


def advance_fanout(version_id: str, stage: StageName) -> tuple[int, int] | None:
    """Count one finished subtask and return `(completed, total)`.

    A Redis hash rather than a `COUNT(*)` over `media_assets`, because the caller is
    one of dozens of concurrent subtasks and this runs on every completion.
    `HINCRBY` is atomic, so two workers finishing in the same instant get 6 and 7
    instead of both reading 6 and both writing 7.

    Display state only: `media_assets` holds the real tally and `GET /stories/{id}/jobs`
    recomputes from it, so losing this to a Redis restart costs a stale number
    rather than correctness. Returns `None` when there is nothing trustworthy to
    report - Redis unavailable, or the stage was never opened - so the caller can
    skip publishing rather than publish a misleading `3/0`.
    """
    try:
        key = _fanout_key(version_id, stage)
        pipe = _client().pipeline(transaction=False)
        pipe.hincrby(key, _DONE, 1)
        pipe.hget(key, _TOTAL)
        pipe.expire(key, STREAM_TTL_SECONDS)
        done, total, _ = pipe.execute()
    except Exception:
        log.warning(
            "fanout_advance_failed", version_id=version_id, stage=stage.value, exc_info=True
        )
        return None

    if total is None:
        return None
    return int(done), int(total)


@dataclass(frozen=True)
class StreamedEvent:
    """One entry read back off a story's stream.

    `event_id` is the Redis stream ID and is what a client echoes back to resume.
    `data` is the raw JSON exactly as published: readers forward it verbatim rather
    than re-serialising, so a field added by a newer worker survives an older API.
    """

    event_id: str
    data: str


async def read_events(
    story_id: str,
    *,
    last_id: str | None,
    idle_timeout_seconds: float,
) -> AsyncGenerator[StreamedEvent | None, None]:
    """Yield a story's events, resuming after `last_id`.

    `last_id` of `None` replays the whole retained log; otherwise it is an exclusive
    lower bound, so a resuming client never re-receives what it already applied.
    Either way the backlog arrives before the read starts blocking, so a client is
    brought fully up to date before it waits on anything.

    Yields `None` whenever `idle_timeout_seconds` elapses with nothing published,
    which is how the caller knows to emit a keepalive. Without that an SSE body can
    sit silent long enough for an intermediary to drop it, and the TTS fan-out on a
    long story is exactly that quiet.

    Typed as a generator rather than an iterator because a caller that stops early
    must be able to `aclose()` this to release the connection; every caller here does.
    """
    client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    key = stream_key(story_id)
    # `xread` treats the cursor as exclusive, so starting at ORIGIN yields
    # everything retained and starting at the client's last ID yields only what it
    # has not seen. No separate backlog pass is needed for either case.
    cursor = last_id or ORIGIN
    # Milliseconds, and an int: redis-py rejects a float `block`.
    block_ms = max(1, int(idle_timeout_seconds * 1000))

    try:
        while True:
            batch = await client.xread({key: cursor}, count=100, block=block_ms)
            if not batch:
                yield None
                continue
            for _stream, entries in batch:
                for event_id, fields in entries:
                    cursor = event_id
                    data = fields.get(_FIELD)
                    if data is not None:
                        yield StreamedEvent(event_id=event_id, data=data)
    finally:
        await client.aclose()


def parse_event(data: str) -> ProgressEvent | None:
    """Validate one raw frame, returning `None` for anything unrecognised.

    For tests and for consumers that want the typed event. The SSE and WebSocket
    endpoints deliberately forward raw JSON instead, so a field added by a newer
    worker is not silently dropped by an older API.
    """
    try:
        return progress_event_adapter.validate_python(json.loads(data))
    except Exception:
        return None
