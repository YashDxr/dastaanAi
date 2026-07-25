"""Tests for the rules that are easy to break silently.

These need no database or network, so they stay fast enough to run on every save.
"""

import pytest
from daastaan_common import ids
from daastaan_common.storage import UnsafeObjectKeyError, validate_key
from daastaan_contracts import (
    PIPELINE_STAGES,
    AssetKind,
    Scope,
    StageName,
    StoryState,
    plan_stages,
)


class TestStagePlanning:
    def test_line_scope_stays_narrow(self):
        """The point of scoped regeneration: one line must not drag image
        generation or the earlier reasoning stages along with it."""
        planned = plan_stages(Scope.LINE, StageName.EMOTION_TAGGING)
        assert planned == (
            StageName.EMOTION_TAGGING,
            StageName.TTS_SYNTHESIS,
            StageName.ASSEMBLY,
        )

    def test_character_scope_reassigns_voice_then_resynthesises(self):
        assert plan_stages(Scope.CHARACTER, StageName.VOICE_ASSIGNMENT) == (
            StageName.VOICE_ASSIGNMENT,
            StageName.TTS_SYNTHESIS,
            StageName.ASSEMBLY,
        )

    def test_full_story_runs_everything(self):
        assert plan_stages(Scope.FULL_STORY, StageName.MOOD_CLASSIFICATION) == PIPELINE_STAGES

    def test_assembly_always_included(self):
        for scope, stage in [
            (Scope.LINE, StageName.TTS_SYNTHESIS),
            (Scope.SCENE, StageName.IMAGE_GENERATION),
        ]:
            assert StageName.ASSEMBLY in plan_stages(scope, stage)

    def test_invalid_entry_point_rejected(self):
        """A line-scoped request cannot re-enter at an arbitrary stage, which is
        what stops a crafted directive from triggering a full regeneration."""
        with pytest.raises(ValueError, match="not a valid entry point"):
            plan_stages(Scope.LINE, StageName.STORY_UNDERSTANDING)

        with pytest.raises(ValueError):
            plan_stages(Scope.CHARACTER, StageName.MOOD_CLASSIFICATION)


class TestObjectKeys:
    def test_generated_keys_are_accepted(self):
        key = ids.object_key("abc123", AssetKind.LINE_AUDIO, line_id=ids.line_id(7))
        assert validate_key(key) == key
        assert key == "abc123/line_audio/line_0007.mp3"

    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc/passwd",
            "abc/../../secret.mp3",
            "/absolute/path.mp3",
            "key with spaces.mp3",
            "key;rm -rf /.mp3",
            "$(whoami).mp3",
        ],
    )
    def test_traversal_and_injection_rejected(self, bad):
        with pytest.raises(UnsafeObjectKeyError):
            validate_key(bad)

    def test_dedupe_key_distinguishes_artifacts(self):
        audio = ids.dedupe_key(AssetKind.LINE_AUDIO, line_id="line_0001")
        image = ids.dedupe_key(AssetKind.SCENE_IMAGE, scene_id="scene_01")
        final = ids.dedupe_key(AssetKind.FINAL_EPISODE)
        assert len({audio, image, final}) == 3


class TestStoryState:
    def test_round_trips_through_json(self):
        """State is persisted as JSONB and rehydrated by every task, so this is
        the load-bearing serialisation path."""
        state = StoryState(
            story_id="s1", version_id="v1", user_id="u1", raw_text="A dream about rain."
        )
        assert StoryState.model_validate(state.model_dump(mode="json")) == state

    def test_lookups_return_none_for_unknown_ids(self):
        state = StoryState(story_id="s1", version_id="v1", user_id="u1", raw_text="x" * 30)
        assert state.line_by_id("line_9999") is None
        assert state.scene_by_id("scene_99") is None
        assert state.character_by_id("char_99") is None
