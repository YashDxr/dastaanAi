"""Streaming a stage's output, and what the user sees while it streams.

Two things are being pinned down. First, that the preview projections cope with
genuinely partial JSON - they run on a half-written object, so the interesting cases
are all the ones where a field has not arrived yet. Second, that switching the
gateway from a blocking parse to a stream did not quietly cost anything: the parsed
result and the cost ledger row have to come out identical, because the ledger is the
only record of what was spent.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from daastaan_agent import previews, repo
from daastaan_agent.gateway import ModelGateway, StreamedDelta
from daastaan_agent.stage_progress import stage_reporter
from daastaan_contracts import StageName, StagePreviewEvent, StageTokensEvent
from daastaan_contracts.models import MoodClassificationOutput
from sqlmodel import Session

# --- projections over partial output --------------------------------------


def test_characters_appear_as_they_are_written() -> None:
    """The list grows as tokens arrive, and each call is a prefix of the next."""
    half_written = {"characters": [{"name": "Lily", "role": "protagonist"}, {"name": "Ru"}]}

    assert previews.preview_items(StageName.CHARACTER_REGISTRY, half_written) == [
        "Lily — protagonist",
        # The role has not arrived yet, so the name stands alone rather than
        # rendering as "Ru — undefined".
        "Ru",
    ]


def test_a_character_with_no_name_yet_is_not_shown() -> None:
    """The first thing a streamed object has is an empty shell."""
    assert previews.preview_items(StageName.CHARACTER_REGISTRY, {"characters": [{}]}) == []


def test_a_trailing_fragment_is_skipped_rather_than_crashing() -> None:
    """Partial parsing leaves the last element as whatever it has so far."""
    parsed = {"characters": [{"name": "Lily"}, "partial-garbage", 7, None]}

    assert previews.preview_items(StageName.CHARACTER_REGISTRY, parsed) == ["Lily"]


def test_the_script_reads_as_dialogue() -> None:
    parsed = {"lines": [{"speaker": "Lily", "text": "What's behind this door?"}]}

    assert previews.preview_items(StageName.DIALOGUE_ATTRIBUTION, parsed) == [
        "Lily: What's behind this door?"
    ]


def test_story_shape_arrives_in_the_order_it_is_written() -> None:
    parsed = {
        "title": "The Enchanted Garden",
        "arc_summary": "A child finds a garden.",
        "scenes": [{"title": "Discovery"}, {"title": "First Meeting"}],
    }

    assert previews.preview_items(StageName.STORY_UNDERSTANDING, parsed) == [
        "The Enchanted Garden",
        "A child finds a garden.",
        "Discovery",
        "First Meeting",
    ]


def test_long_text_is_truncated_so_a_preview_stays_a_preview() -> None:
    parsed = {"lines": [{"speaker": "N", "text": "word " * 200}]}

    (item,) = previews.preview_items(StageName.DIALOGUE_ATTRIBUTION, parsed)
    assert len(item) <= 95
    assert item.endswith("…")


def test_whitespace_is_collapsed() -> None:
    parsed = {"title": "  The\n\n  Garden  ", "scenes": []}

    assert previews.preview_items(StageName.STORY_UNDERSTANDING, parsed) == ["The Garden"]


@pytest.mark.parametrize("parsed", [None, "", 7, [], {"unexpected": "shape"}])
def test_nothing_useful_yields_nothing(parsed: Any) -> None:
    """`parsed` is `None` until enough JSON has arrived to parse a prefix."""
    assert previews.preview_items(StageName.CHARACTER_REGISTRY, parsed) == []


def test_stages_without_a_projection_are_silent() -> None:
    """Emotion tags mean nothing without the line beside them, so they are not shown."""
    assert previews.preview_label(StageName.EMOTION_TAGGING) is None
    assert previews.preview_items(StageName.EMOTION_TAGGING, {"tags": [{"emotion": "sad"}]}) == []


# --- the reporter ---------------------------------------------------------


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    captured: list[Any] = []
    monkeypatch.setattr(repo, "publish", lambda story_id, event: captured.append(event))
    return captured


def test_each_preview_item_is_sent_exactly_once(published: list[Any]) -> None:
    """The client appends what it is told, so a resend would duplicate on screen."""
    report = stage_reporter("s1", StageName.CHARACTER_REGISTRY)

    report(StreamedDelta(parsed={"characters": [{"name": "Lily"}]}, tokens=10))
    report(StreamedDelta(parsed={"characters": [{"name": "Lily"}, {"name": "Rufus"}]}, tokens=20))

    assert [event.items for event in published] == [["Lily"], ["Rufus"]]
    assert all(isinstance(event, StagePreviewEvent) for event in published)
    assert published[0].label == "Casting characters"


def test_a_delta_that_adds_nothing_publishes_nothing(published: list[Any]) -> None:
    report = stage_reporter("s1", StageName.CHARACTER_REGISTRY)
    delta = StreamedDelta(parsed={"characters": [{"name": "Lily"}]}, tokens=10)

    report(delta)
    report(delta)

    assert len(published) == 1


def test_content_is_preferred_over_the_token_counter(published: list[Any]) -> None:
    """Publishing both would double the traffic to say the same thing."""
    report = stage_reporter("s1", StageName.CHARACTER_REGISTRY)

    report(StreamedDelta(parsed={"characters": [{"name": "Lily"}]}, tokens=10))

    assert len(published) == 1
    assert isinstance(published[0], StagePreviewEvent)


def test_a_stage_with_nothing_to_show_still_looks_alive(published: list[Any]) -> None:
    report = stage_reporter("s1", StageName.EMOTION_TAGGING)

    report(StreamedDelta(parsed={"tags": []}, tokens=40))
    report(StreamedDelta(parsed={"tags": []}, tokens=90))

    assert [event.tokens for event in published] == [40, 90]
    assert all(isinstance(event, StageTokensEvent) for event in published)


def test_the_token_counter_never_goes_backwards(published: list[Any]) -> None:
    """A retried attempt restarts the estimate, and a counter that falls looks stalled."""
    report = stage_reporter("s1", StageName.EMOTION_TAGGING)

    report(StreamedDelta(parsed=None, tokens=90))
    report(StreamedDelta(parsed=None, tokens=12))

    assert [event.tokens for event in published] == [90]


# --- the gateway ----------------------------------------------------------


class FakeStreamEvent:
    def __init__(self, delta: str, snapshot: str, parsed: Any) -> None:
        self.type = "content.delta"
        self.delta = delta
        self.snapshot = snapshot
        self.parsed = parsed


class FakeStream:
    """Stands in for the SDK's structured-output stream context manager."""

    def __init__(self, script: list[Any], final: Any) -> None:
        self.script = script
        self.final = final

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def __iter__(self) -> Any:
        return iter(self.script)

    def get_final_completion(self) -> Any:
        return self.final


def _completion(parsed: Any, *, prompt: int = 100, completion: int = 50) -> Any:
    message = MagicMock()
    message.parsed = parsed
    choice = MagicMock()
    choice.message = message
    result = MagicMock()
    result.choices = [choice]
    result.usage = MagicMock(prompt_tokens=prompt, completion_tokens=completion)
    return result


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> ModelGateway:
    """A gateway whose OpenAI client and side effects are all stubbed out."""
    from daastaan_agent import gateway as gateway_module

    monkeypatch.setattr(gateway_module, "OpenAI", lambda **kwargs: MagicMock())
    monkeypatch.setattr(gateway_module.cache, "enabled", lambda: False)
    monkeypatch.setattr(gateway_module.cache, "put_llm", lambda *a, **k: None)

    session = MagicMock(spec=Session)
    session.get.return_value = None
    return ModelGateway(session, stage=StageName.MOOD_CLASSIFICATION.value)


def _run(gateway: ModelGateway, script: list[Any], **kwargs: Any) -> Any:
    expected = MoodClassificationOutput(
        genre="fantasy", mood="whimsical", tone_keywords=["warm"], pacing="moderate"
    )
    gateway.client.chat.completions.stream = MagicMock(
        return_value=FakeStream(script, _completion(expected))
    )
    result = gateway.structured(
        schema=MoodClassificationOutput, system="sys", user_content="user", **kwargs
    )
    return result, expected


def test_streaming_returns_the_same_parsed_object_as_before(gateway: ModelGateway) -> None:
    result, expected = _run(gateway, [FakeStreamEvent("{", "{", None)])

    assert result == expected


def test_usage_is_requested_so_the_ledger_stays_exact(gateway: ModelGateway) -> None:
    """Streaming omits usage unless asked, which would silently turn every
    reasoning call in the budget dashboard into an estimate."""
    _run(gateway, [])

    kwargs = gateway.client.chat.completions.stream.call_args.kwargs
    assert kwargs["stream_options"] == {"include_usage": True}
    assert kwargs["response_format"] is MoodClassificationOutput
    # Instructions and untrusted text stay in separate roles.
    assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]


def test_the_cost_ledger_records_real_token_counts(gateway: ModelGateway) -> None:
    recorded: list[dict[str, Any]] = []
    gateway._record = lambda **kwargs: recorded.append(kwargs)  # type: ignore[method-assign]

    _run(gateway, [FakeStreamEvent("{", "{", None)])

    assert len(recorded) == 1
    assert recorded[0]["input_tokens"] == 100
    assert recorded[0]["output_tokens"] == 50


def test_the_last_delta_is_always_delivered(gateway: ModelGateway) -> None:
    """The throttle would otherwise swallow the frame holding the finished object."""
    seen: list[StreamedDelta] = []
    script = [
        FakeStreamEvent("a", '{"genre":', None),
        FakeStreamEvent("b", '{"genre": "fantasy"}', {"genre": "fantasy"}),
    ]

    _run(gateway, script, on_delta=seen.append)

    assert seen[-1].parsed == {"genre": "fantasy"}


def test_deltas_are_throttled_rather_than_forwarded_per_token(
    gateway: ModelGateway,
) -> None:
    """A 900-token stage would otherwise be nearly a thousand Redis publishes."""
    seen: list[StreamedDelta] = []
    script = [FakeStreamEvent("x", "x" * i, None) for i in range(1, 51)]

    _run(gateway, script, on_delta=seen.append)

    # One that opens the throttle window, plus the guaranteed final flush.
    assert len(seen) == 2


def test_a_broken_callback_does_not_discard_a_paid_completion(
    gateway: ModelGateway,
) -> None:
    """Losing a progress frame is cheap. Losing the result is not, and raising
    inside the retry decorator could pay for it a second time."""

    def explode(_delta: StreamedDelta) -> None:
        raise RuntimeError("progress bar is on fire")

    result, expected = _run(gateway, [FakeStreamEvent("{", "{", None)], on_delta=explode)

    assert result == expected


def test_a_constructor_callback_is_used_without_the_node_opting_in(
    gateway: ModelGateway,
) -> None:
    """How the pipeline wires previews for every stage without touching nodes."""
    seen: list[StreamedDelta] = []
    gateway.on_delta = seen.append

    _run(gateway, [FakeStreamEvent("{", "{", {"genre": "f"})])

    assert len(seen) == 1


def test_a_cache_hit_skips_the_stream_entirely(
    gateway: ModelGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no generation to watch, and the stage completes immediately."""
    from daastaan_agent import gateway as gateway_module

    cached = MoodClassificationOutput(
        genre="noir", mood="bleak", tone_keywords=["cold"], pacing="slow"
    )
    monkeypatch.setattr(gateway_module.cache, "enabled", lambda: True)
    monkeypatch.setattr(gateway_module.cache, "get_llm", lambda key: cached.model_dump(mode="json"))
    gateway.client.chat.completions.stream = MagicMock()
    seen: list[StreamedDelta] = []

    result = gateway.structured(
        schema=MoodClassificationOutput, system="s", user_content="u", on_delta=seen.append
    )

    assert result == cached
    assert seen == []
    gateway.client.chat.completions.stream.assert_not_called()
