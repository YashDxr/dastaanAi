"""What a regeneration keeps from its parent version.

Media assets are scoped to a version, so a fork that copies nothing forward makes
the scene images disappear from the studio and re-bills every line of TTS to
respeak one of them. This pins down exactly which artifacts a scoped regeneration
is allowed to throw away.
"""

import pytest
from daastaan_common.models import MediaAsset, StoryVersion
from daastaan_common.versions import carry_over_assets, invalidated_dedupe_keys
from daastaan_contracts import AssetKind, Scope, StageName, StoryState, plan_stages
from sqlmodel import select


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
                    "scene_id": "scene_00" if i < 2 else "scene_01",
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

    def test_story_time_machine_drops_all_future_artwork(self, state):
        """A narrative branch preserves its prefix but invalidates the selected
        scene and suffix, whose content may now be entirely different."""
        stale = _invalidated(state, Scope.SCENE, StageName.STORY_UNDERSTANDING, "scene_01")
        assert "scene_image:scene_01" in stale
        assert "scene_image:scene_00" not in stale
        assert stale >= {"line_audio:line_0002", "line_audio:line_0003"}
        assert "line_audio:line_0000" not in stale
        assert "line_audio:line_0001" not in stale


def test_child_keeps_prefix_media_but_never_terminal_outputs(in_memory_session, state):
    """Final mixes, videos, and downloaded variants must not make a child look
    complete before its own assembly. Reusable prefix source media may share the
    immutable object bytes safely."""
    parent = StoryVersion(
        story_id="story_1",
        version_number=1,
        state_json=state.model_dump(mode="json"),
    )
    child = StoryVersion(story_id="story_1", version_number=2, state_json={})
    in_memory_session.add(parent)
    in_memory_session.add(child)
    in_memory_session.flush()
    in_memory_session.add_all(
        [
            MediaAsset(
                version_id=parent.id,
                dedupe_key="line_audio:line_0000",
                kind=AssetKind.LINE_AUDIO.value,
                object_key="parent/prefix.mp3",
                line_id="line_0000",
            ),
            MediaAsset(
                version_id=parent.id,
                dedupe_key="line_audio:line_0002",
                kind=AssetKind.LINE_AUDIO.value,
                object_key="parent/future.mp3",
                line_id="line_0002",
            ),
            MediaAsset(
                version_id=parent.id,
                dedupe_key="scene_image:scene_00",
                kind=AssetKind.SCENE_IMAGE.value,
                object_key="parent/prefix.png",
                scene_id="scene_00",
            ),
            MediaAsset(
                version_id=parent.id,
                dedupe_key="scene_image:scene_01",
                kind=AssetKind.SCENE_IMAGE.value,
                object_key="parent/future.png",
                scene_id="scene_01",
            ),
            MediaAsset(
                version_id=parent.id,
                dedupe_key="final_episode:single",
                kind=AssetKind.FINAL_EPISODE.value,
                object_key="parent/final.mp3",
            ),
            MediaAsset(
                version_id=parent.id,
                dedupe_key="final_video:single",
                kind=AssetKind.FINAL_VIDEO.value,
                object_key="parent/final.mp4",
            ),
            MediaAsset(
                version_id=parent.id,
                dedupe_key="episode_export:m4a",
                kind=AssetKind.EPISODE_EXPORT.value,
                object_key="parent/export.m4a",
            ),
        ]
    )
    in_memory_session.commit()

    copied = carry_over_assets(
        in_memory_session,
        parent_version_id=parent.id,
        child_version_id=child.id,
        state=state,
        scope=Scope.SCENE,
        planned=plan_stages(Scope.SCENE, StageName.STORY_UNDERSTANDING),
        target_id="scene_01",
    )
    inherited = list(
        in_memory_session.exec(
            select(MediaAsset).where(MediaAsset.version_id == child.id)
        ).all()
    )
    assert copied == 2
    assert {asset.dedupe_key for asset in inherited} == {
        "line_audio:line_0000",
        "scene_image:scene_00",
    }


class TestFullStory:
    def test_keeps_nothing(self, state):
        stale = _invalidated(state, Scope.FULL_STORY, StageName.MOOD_CLASSIFICATION, None)
        expected = {f"line_audio:line_{i:04d}" for i in range(4)}
        expected |= {f"scene_image:scene_{i:02d}" for i in range(2)}
        expected.add("music_bed:single")
        assert stale == expected


class TestMusicScope:
    def test_music_only_drops_the_bed_not_narration_or_images(self, state):
        stale = _invalidated(state, Scope.MUSIC, StageName.MUSIC_GENERATION, None)
        assert stale == {"music_bed:single"}
