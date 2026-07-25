"""Unit tests for the Dastaan music-worker boundary."""

from __future__ import annotations

import io
import wave
from types import SimpleNamespace

import pytest
from daastaan_agent.music import (
    MusicServiceClient,
    MusicServiceError,
    build_music_brief,
    validate_wav,
)
from daastaan_contracts import MoodClassificationOutput, Scene, StoryState, ValidatedDirective


def _wav(*, channels: int = 2, sample_rate: int = 44_100, seconds: int = 5) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * channels * sample_rate * seconds)
    return output.getvalue()


def test_music_brief_is_bounded_and_retry_deterministic():
    state = StoryState(
        story_id="story-1",
        version_id="version-1",
        user_id="user-1",
        raw_text="A short test story.",
        scenes=[
            Scene(
                id="scene_00",
                index=0,
                title="Arrival",
                summary="A traveler arrives at dawn.",
                setting="A valley",
                mood_tag="hopeful",
            )
        ],
    )
    state.mood = MoodClassificationOutput(
        genre="fantasy",
        mood="wonder",
        tone_keywords=["warm", "magical"],
        pacing="gentle",
    )
    settings = SimpleNamespace(music_duration_seconds=5)

    first = build_music_brief(state, settings)
    second = build_music_brief(state, settings)

    assert first == second
    assert first.duration_seconds == 5
    assert "Instrumental" in first.prompt
    assert len(first.prompt) <= 1000


def test_music_feedback_does_not_pass_artist_imitation_to_the_model():
    state = StoryState(
        story_id="story-1",
        version_id="version-1",
        user_id="user-1",
        raw_text="A short test story.",
    )
    state.regen = ValidatedDirective(
        scope="music",
        target_stage="music_generation",
        target_id=None,
        instruction_delta="Make it sound like a famous artist's song with vocals.",
    )
    brief = build_music_brief(state, SimpleNamespace(music_duration_seconds=5))

    assert "famous artist" not in brief.prompt.lower()
    assert "without imitating an artist" in brief.prompt


def test_validate_wav_accepts_expected_sidecar_format():
    assert validate_wav(_wav()) == 5_000


def test_validate_wav_rejects_mono_response():
    with pytest.raises(MusicServiceError, match="44.1 kHz, stereo"):
        validate_wav(_wav(channels=1))


def test_music_client_does_not_inherit_proxy_environment():
    settings = SimpleNamespace(
        music_service_base_url="http://127.0.0.1:8787",
        music_service_token="t" * 32,
        music_service_hmac_secret="s" * 32,
        music_service_timeout_seconds=60,
        music_service_poll_interval_seconds=1.0,
        music_duration_seconds=5,
    )
    with MusicServiceClient(settings) as client:
        assert client._client._trust_env is False
