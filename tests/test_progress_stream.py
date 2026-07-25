"""The progress event contract and its transport.

No Redis and no API process: the bus is exercised against a fake client that
records the commands it is given, and the SSE body is rendered from a scripted
reader. What is being pinned down here is the behaviour the browser depends on -
that events survive a round trip unchanged, that a reconnect resumes rather than
replays, and that a publish can never take a pipeline down with it.
"""

from typing import Any

import pytest
from daastaan_common import events
from daastaan_common.models import MediaAsset, Story, StoryVersion
from daastaan_contracts import (
    AssetEvent,
    AssetKind,
    CompleteEvent,
    FeedbackEvent,
    FeedbackStatus,
    HeartbeatEvent,
    JobStatus,
    MusicStatusEvent,
    Scope,
    StageEvent,
    StageName,
    StagePreviewEvent,
    StageProgressEvent,
    StageTokensEvent,
    progress_event_adapter,
)

# --- fakes ----------------------------------------------------------------


class FakePipeline:
    """Records queued commands and returns one canned reply per command."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _queue(self, name: str, *args: Any, **kwargs: Any) -> "FakePipeline":
        self.calls.append((name, args, kwargs))
        return self

    def __getattr__(self, name: str) -> Any:
        return lambda *args, **kwargs: self._queue(name, *args, **kwargs)

    def execute(self) -> list[Any]:
        return [self.replies.get(name) for name, _, _ in self.calls]


class FakeRedis:
    def __init__(self, replies: dict[str, Any] | None = None, fail: bool = False) -> None:
        self.replies = replies or {}
        self.fail = fail
        self.pipelines: list[FakePipeline] = []
        self.deleted: list[str] = []

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        if self.fail:
            raise RuntimeError("redis is down")
        pipe = FakePipeline(self.replies)
        self.pipelines.append(pipe)
        return pipe

    def delete(self, key: str) -> None:
        if self.fail:
            raise RuntimeError("redis is down")
        self.deleted.append(key)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(events, "_client", lambda: client)
    return client


# --- the wire format ------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        StageEvent(stage=StageName.ASSEMBLY, status=JobStatus.RUNNING),
        StageEvent(stage=StageName.TTS_SYNTHESIS, status=JobStatus.FAILED, error="boom"),
        StageProgressEvent(stage=StageName.TTS_SYNTHESIS, completed=3, total=12),
        StagePreviewEvent(stage=StageName.CHARACTER_REGISTRY, label="Cast", items=["Lily"]),
        StageTokensEvent(stage=StageName.STORY_UNDERSTANDING, tokens=140),
        AssetEvent(kind=AssetKind.LINE_AUDIO, line_id="line_0001"),
        AssetEvent(kind=AssetKind.MUSIC_BED, duration_ms=30_000),
        MusicStatusEvent(status="unavailable", error="Score unavailable."),
        FeedbackEvent(
            status=FeedbackStatus.APPLIED,
            feedback_id="fb_1",
            scope=Scope.LINE,
            target_stage=StageName.TTS_SYNTHESIS,
        ),
        CompleteEvent(version_id="ver_1"),
        HeartbeatEvent(),
    ],
)
def test_every_event_survives_a_round_trip(event: Any) -> None:
    raw = progress_event_adapter.dump_json(event).decode()
    assert events.parse_event(raw) == event


def test_unknown_event_type_is_rejected_not_guessed() -> None:
    """A frame the API does not recognise must not be coerced into one it does."""
    assert events.parse_event('{"type": "telepathy", "stage": "assembly"}') is None
    assert events.parse_event("not json at all") is None


def test_progress_event_is_missing_required_fields() -> None:
    assert events.parse_event('{"type": "stage_progress", "stage": "assembly"}') is None


def test_counts_cannot_be_negative() -> None:
    with pytest.raises(ValueError):
        StageProgressEvent(stage=StageName.TTS_SYNTHESIS, completed=-1, total=4)


# --- publishing -----------------------------------------------------------


def test_publish_appends_a_capped_expiring_entry(fake_redis: FakeRedis) -> None:
    events.publish("story_1", CompleteEvent(version_id="ver_9"))

    (pipe,) = fake_redis.pipelines
    names = [name for name, _, _ in pipe.calls]
    assert names == ["xadd", "expire"]

    _, (key, payload), kwargs = pipe.calls[0]
    assert key == "daastaan:events:story_1"
    # Capped so one runaway story cannot grow without limit, and approximate
    # because exact trimming is the expensive variant.
    assert kwargs["maxlen"] == events.MAX_STREAM_LENGTH
    assert kwargs["approximate"] is True
    assert events.parse_event(payload["data"]) == CompleteEvent(version_id="ver_9")

    assert pipe.calls[1][1] == ("daastaan:events:story_1", events.STREAM_TTL_SECONDS)


def test_publish_never_raises_into_the_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis being down degrades the UI to polling. It must not fail a paid stage."""
    monkeypatch.setattr(events, "_client", lambda: FakeRedis(fail=True))
    events.publish("story_1", CompleteEvent(version_id="ver_9"))


# --- fan-out counters -----------------------------------------------------


def test_open_fanout_seeds_carried_over_work(fake_redis: FakeRedis) -> None:
    """A regeneration inherits most of its assets, and the bar must say so."""
    events.open_fanout("ver_1", StageName.TTS_SYNTHESIS, total=40, completed=39)

    (pipe,) = fake_redis.pipelines
    assert [name for name, _, _ in pipe.calls] == ["delete", "hset", "expire"]
    assert pipe.calls[1][2]["mapping"] == {"total": 40, "done": 39}


def test_advance_fanout_returns_the_new_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeRedis(replies={"hincrby": 4, "hget": "12"})
    monkeypatch.setattr(events, "_client", lambda: client)

    assert events.advance_fanout("ver_1", StageName.TTS_SYNTHESIS) == (4, 12)


def test_advance_fanout_reports_nothing_when_the_stage_was_never_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a total there is no honest fraction to publish, so say nothing."""
    client = FakeRedis(replies={"hincrby": 1, "hget": None})
    monkeypatch.setattr(events, "_client", lambda: client)

    assert events.advance_fanout("ver_1", StageName.TTS_SYNTHESIS) is None


def test_advance_fanout_survives_a_dead_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "_client", lambda: FakeRedis(fail=True))
    assert events.advance_fanout("ver_1", StageName.TTS_SYNTHESIS) is None


# --- reading the stream ---------------------------------------------------


class FakeAsyncRedis:
    """Replays a scripted list of `xread` results, then reports idleness."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.reads: list[dict[str, str]] = []
        self.closed = False

    async def xread(self, streams: dict[str, str], count: int, block: int) -> Any:
        self.reads.append(dict(streams))
        return self.script.pop(0) if self.script else []

    async def aclose(self) -> None:
        self.closed = True


def _install_reader(monkeypatch: pytest.MonkeyPatch, script: list[Any]) -> FakeAsyncRedis:
    client = FakeAsyncRedis(script)
    monkeypatch.setattr(events.aioredis, "from_url", lambda *a, **k: client)
    return client


async def _drain(story_id: str, last_id: str | None, limit: int) -> list[Any]:
    collected: list[Any] = []
    async for item in events.read_events(story_id, last_id=last_id, idle_timeout_seconds=0.01):
        collected.append(item)
        if len(collected) >= limit:
            break
    return collected


async def test_a_fresh_listener_replays_the_retained_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page opened mid-run rebuilds the run so far, previews included."""
    entry = ("daastaan:events:s1", [("1-0", {"data": '{"type":"heartbeat"}'})])
    client = _install_reader(monkeypatch, [[entry]])

    got = await _drain("s1", None, 1)

    assert [item.event_id for item in got] == ["1-0"]
    # ORIGIN, not "$": starting from "now" is what made a reload show an empty
    # stepper until the next stage happened to finish.
    assert client.reads[0] == {"daastaan:events:s1": events.ORIGIN}


async def test_a_reconnecting_listener_resumes_after_its_last_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = ("daastaan:events:s1", [("7-0", {"data": '{"type":"heartbeat"}'})])
    client = _install_reader(monkeypatch, [[entry]])

    await _drain("s1", "5-3", 1)

    assert client.reads[0] == {"daastaan:events:s1": "5-3"}


async def test_the_cursor_advances_so_events_arrive_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ("daastaan:events:s1", [("1-0", {"data": '{"type":"heartbeat"}'})])
    second = ("daastaan:events:s1", [("2-0", {"data": '{"type":"heartbeat"}'})])
    client = _install_reader(monkeypatch, [[first], [second]])

    await _drain("s1", None, 2)

    assert client.reads[1] == {"daastaan:events:s1": "1-0"}


async def test_an_idle_stream_yields_a_keepalive_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller needs a nudge to write a ping, or a proxy drops a quiet stream."""
    _install_reader(monkeypatch, [[]])

    assert await _drain("s1", None, 1) == [None]


# --- the SSE body ---------------------------------------------------------


class FakeRequest:
    def __init__(self, disconnect_after: int = 99) -> None:
        self.checks = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.checks > self.disconnect_after


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("1700000000000-0", "1700000000000-0"),
        (None, None),
        ("", None),
        # Interpolated into a Redis command, so anything not shaped like a stream
        # id is treated as a fresh connection rather than passed through.
        ("$", None),
        ("0-0 MAXLEN", None),
        ("not-an-id", None),
        ("-", None),
    ],
)
def test_only_a_real_stream_id_resumes_a_connection(
    header: str | None, expected: str | None
) -> None:
    from daastaan_api.routers.progress import _resume_from

    assert _resume_from(header) == expected


async def test_sse_body_carries_ids_so_the_browser_can_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daastaan_api.routers import progress

    async def fake_read(story_id: str, *, last_id: str | None, idle_timeout_seconds: float) -> Any:
        yield events.StreamedEvent(event_id="4-0", data='{"type":"heartbeat"}')
        yield None

    monkeypatch.setattr(progress.events, "read_events", fake_read)

    frames = [frame async for frame in progress._event_stream("s1", FakeRequest(), None)]

    assert frames[0] == "retry: 3000\n\n"
    # The id line is the whole mechanism behind gapless reconnection.
    assert frames[1] == 'id: 4-0\ndata: {"type":"heartbeat"}\n\n'
    assert frames[2] == ": ping\n\n"


async def test_sse_body_stops_when_the_listener_goes_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise the reader stays parked on a stream nobody is listening to."""
    from daastaan_api.routers import progress

    async def fake_read(story_id: str, *, last_id: str | None, idle_timeout_seconds: float) -> Any:
        while True:
            yield events.StreamedEvent(event_id="1-0", data='{"type":"heartbeat"}')

    monkeypatch.setattr(progress.events, "read_events", fake_read)

    frames = [
        frame async for frame in progress._event_stream("s1", FakeRequest(disconnect_after=2), None)
    ]

    assert len(frames) == 3  # retry, two events, then the disconnect is noticed


async def test_the_reader_is_released_when_the_listener_goes_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leaving the loop has to close the reader, not wait for a collection to do it.

    `read_events` holds a Redis connection until its `finally` runs. Abandoning the
    generator instead of closing it defers that to garbage collection, so a client
    that reloads a few times would stack up connections it no longer reads.
    """
    from daastaan_api.routers import progress

    released = False

    async def fake_read(story_id: str, *, last_id: str | None, idle_timeout_seconds: float) -> Any:
        nonlocal released
        try:
            while True:
                yield events.StreamedEvent(event_id="1-0", data='{"type":"heartbeat"}')
        finally:
            released = True

    monkeypatch.setattr(progress.events, "read_events", fake_read)

    async for _ in progress._event_stream("s1", FakeRequest(disconnect_after=1), None):
        pass

    assert released


def test_the_event_union_is_published_for_the_frontend() -> None:
    """The browser's event types are generated from these, not hand-written."""
    from daastaan_api.routers.progress import EVENT_SCHEMA_NAME, event_component_schemas

    schemas = event_component_schemas()
    assert EVENT_SCHEMA_NAME in schemas
    assert "StagePreviewEvent" in schemas
    # Every variant reachable, and by a ref the type generator can resolve.
    refs = str(schemas[EVENT_SCHEMA_NAME])
    assert "#/components/schemas/StagePreviewEvent" in refs
    assert "$defs" not in refs


# --- the polling fallback -------------------------------------------------


def _story_with_assets(session: Any, *, assets: list[MediaAsset], lines: int, scenes: int) -> Any:
    state = {
        "story_id": "s1",
        "version_id": "v1",
        "user_id": "u1",
        "raw_text": "x" * 40,
        "lines": [
            {
                "id": f"line_{i}",
                "scene_id": "scene_0",
                "index": i,
                "speaker": "Narrator",
                "text": "hello",
                "line_type": "narration",
            }
            for i in range(lines)
        ],
        "scenes": [
            {
                "id": f"scene_{i}",
                "index": i,
                "title": "t",
                "summary": "s",
                "setting": "x",
                "mood_tag": "m",
            }
            for i in range(scenes)
        ],
    }
    session.add(Story(id="s1", user_id="u1", status="generating", current_version_id="v1"))
    version = StoryVersion(id="v1", story_id="s1", version_number=1, state_json=state)
    session.add(version)
    for asset in assets:
        session.add(asset)
    session.commit()
    return version


def test_polled_progress_counts_finished_media(in_memory_session: Any) -> None:
    from daastaan_api.routers.stories import _fanout_progress

    version = _story_with_assets(
        in_memory_session,
        assets=[
            MediaAsset(
                version_id="v1",
                kind="line_audio",
                dedupe_key="a",
                object_key="k1",
                line_id="line_0",
            ),
            MediaAsset(
                version_id="v1",
                kind="scene_image",
                dedupe_key="b",
                object_key="k2",
                scene_id="scene_0",
            ),
        ],
        lines=3,
        scenes=2,
    )

    by_stage = {p.stage: p for p in _fanout_progress(in_memory_session, version)}

    assert (by_stage["tts_synthesis"].completed, by_stage["tts_synthesis"].total) == (1, 3)
    assert (by_stage["image_generation"].completed, by_stage["image_generation"].total) == (1, 2)


def test_polled_progress_ignores_unfinished_claims(in_memory_session: Any) -> None:
    """`claim_asset` commits an empty key before paying, so counting those rows
    would report a line as recorded the instant its generation started."""
    from daastaan_api.routers.stories import _fanout_progress

    version = _story_with_assets(
        in_memory_session,
        assets=[
            MediaAsset(version_id="v1", kind="line_audio", dedupe_key="a", object_key=""),
        ],
        lines=2,
        scenes=0,
    )

    by_stage = {p.stage: p for p in _fanout_progress(in_memory_session, version)}

    assert by_stage["tts_synthesis"].completed == 0
    # A story with no scenes has no image stage to report on.
    assert "image_generation" not in by_stage


def test_polled_progress_tolerates_an_unreadable_version(in_memory_session: Any) -> None:
    from daastaan_api.routers.stories import _fanout_progress

    version = StoryVersion(id="v2", story_id="s1", version_number=1, state_json={"junk": True})

    assert _fanout_progress(in_memory_session, version) == []
    assert _fanout_progress(in_memory_session, None) == []
