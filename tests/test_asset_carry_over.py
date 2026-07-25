"""What a regeneration keeps from its parent version.

Media assets are scoped to a version, so a fork that copies nothing forward makes
the scene images disappear from the studio and re-bills every line of TTS to
respeak one of them. This pins down exactly which artifacts a scoped regeneration
is allowed to throw away.
"""

import pytest
from daastaan_common.versions import invalidated_dedupe_keys
from daastaan_contracts import Scope, StageName, StoryState, plan_stages


@pytest.fixture
def state() -> StoryState:
    return StoryState.model_validate(
        {
            "story_id": "story_1",
            "version_id": "version_1",
            "user_id": "user_1",
            "raw_text": "a test story",
            "scenes": [
                {
                    "id": f"scene_{i:02d}",
                    "index": i,
                    "title": f"Scene {i}",
                    "summary": "s",
                    "setting": "s",
                    "mood_tag": "calm",
                }
                for i in range(2)
            ],
            "characters": [
                {
                    "id": "char_00",
                    "name": "Ana",
                    "role": "protagonist",
                    "personality": "p",
                    "sample_line": "l",
                },
                {
                    "id": "char_01",
                    "name": "Bo",
                    "role": "supporting",
                    "personality": "p",
                    "sample_line": "l",
                },
            ],
            "lines": [
                {
                    "id": f"line_{i:04d}",
                    "scene_id": "scene_00",
                    "index": i,
                    "speaker": "Ana" if i % 2 == 0 else "Bo",
                    "character_id": "char_00" if i % 2 == 0 else "char_01",
                    "text": "hello",
                    "line_type": "dialogue",
                }
                for i in range(4)
            ],
        }
    )


def _invalidated(state: StoryState, scope: Scope, stage: StageName, target: str | None):
    return invalidated_dedupe_keys(
        state, scope=scope, planned=plan_stages(scope, stage), target_id=target
    )


class TestLineScope:
    def test_only_the_target_line_is_dropped(self, state):
        stale = _invalidated(state, Scope.LINE, StageName.TTS_SYNTHESIS, "line_0002")
        assert stale == {"line_audio:line_0002"}

    def test_scene_images_survive(self, state):
        """The complaint that started this: respeaking one line wiped the artwork."""
        stale = _invalidated(state, Scope.LINE, StageName.TTS_SYNTHESIS, "line_0002")
        assert not any(key.startswith("scene_image:") for key in stale)

    def test_emotion_entry_still_only_touches_its_line(self, state):
        stale = _invalidated(state, Scope.LINE, StageName.EMOTION_TAGGING, "line_0001")
        assert stale == {"line_audio:line_0001"}


class TestCharacterScope:
    def test_drops_every_line_that_character_speaks(self, state):
        stale = _invalidated(state, Scope.CHARACTER, StageName.TTS_SYNTHESIS, "char_01")
        assert stale == {"line_audio:line_0001", "line_audio:line_0003"}


class TestSceneScope:
    def test_drops_the_targeted_image(self, state):
        stale = _invalidated(state, Scope.SCENE, StageName.IMAGE_GENERATION, "scene_01")
        assert "scene_image:scene_01" in stale
        assert "scene_image:scene_00" not in stale


class TestFullStory:
    def test_keeps_nothing(self, state):
        stale = _invalidated(state, Scope.FULL_STORY, StageName.MOOD_CLASSIFICATION, None)
        expected = {f"line_audio:line_{i:04d}" for i in range(4)}
        # Base scene keys, per-shot-type variants, and per-line keys (video mode).
        # invalidated_dedupe_keys generates all three so a carry-over never leaks an
        # old artifact into a fresh run regardless of which generation path was used.
        for i in range(2):
            expected.add(f"scene_image:scene_{i:02d}")
            for tag in ("wide", "mid", "close"):
                expected.add(f"scene_image:scene_{i:02d}:{tag}")
        # All four lines are in scene_00 so they all fall inside the invalidated set.
        expected |= {f"scene_image:line_{i:04d}" for i in range(4)}
        expected.add("music_bed:single")
        assert stale == expected


class TestMusicScope:
    def test_music_only_drops_the_bed_not_narration_or_images(self, state):
        stale = _invalidated(state, Scope.MUSIC, StageName.MUSIC_GENERATION, None)
        assert stale == {"music_bed:single"}
