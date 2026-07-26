"""The explicit API path used by the Story Time Machine UI.

The browser only chooses a scene and supplies a creative note. The API must keep
owning the target validation, parent/child version fork, and stage plan so a
crafted browser request cannot broaden the pipeline.
"""

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from daastaan_agent.tasks import _mark_story
from daastaan_api.routers.stories import regenerate
from daastaan_api.schemas import RegenerateRequest
from daastaan_common.models import Story, StoryVersion, User
from daastaan_contracts import PIPELINE_STAGES, Scope, StageName, StoryState, StoryStatus
from fastapi import HTTPException
from sqlmodel import select


def _state_for(story: Story, user: User) -> StoryState:
    return StoryState.model_validate(
        {
            "story_id": story.id,
            "version_id": "placeholder",
            "user_id": user.id,
            "raw_text": "A story long enough to satisfy the domain contract.",
            "final_episode_key": "parent/final.mp3",
            "final_video_key": "parent/final.mp4",
            "scenes": [
                {
                    "id": "scene_00",
                    "index": 0,
                    "title": "The promise",
                    "summary": "Mira agrees to carry the letter.",
                    "setting": "The market",
                    "mood_tag": "hopeful",
                },
                {
                    "id": "scene_01",
                    "index": 1,
                    "title": "The crossroads",
                    "summary": "Mira takes the forest path.",
                    "setting": "The edge of town",
                    "mood_tag": "tense",
                },
            ],
        }
    )


def _add_version(
    session, story: Story, state: StoryState, number: int, parent_id: str | None = None
) -> StoryVersion:  # type: ignore[no-untyped-def]
    version = StoryVersion(
        story_id=story.id,
        parent_version_id=parent_id,
        version_number=number,
        state_json=state.model_dump(mode="json"),
    )
    session.add(version)
    session.flush()
    version.state_json["version_id"] = version.id
    return version


@contextmanager
def _regen_patches():
    with (
        patch("daastaan_api.routers.stories.enforce_rate_limit"),
        patch("daastaan_api.routers.stories.enforce_budget"),
        patch("daastaan_api.routers.stories.audit"),
        patch(
            "daastaan_api.routers.stories.dispatch_regeneration", return_value="queued"
        ) as dispatch,
    ):
        yield dispatch


def test_scene_branch_forks_from_current_version_and_rebuilds_future(in_memory_session):
    user = User(email="listener@example.test", password_hash="not-used-in-this-test")  # noqa: S106
    in_memory_session.add(user)
    in_memory_session.flush()

    story = Story(user_id=user.id, status=StoryStatus.READY)
    in_memory_session.add(story)
    in_memory_session.flush()

    state = _state_for(story, user)
    parent = _add_version(in_memory_session, story, state, 1)
    story.current_version_id = parent.id
    in_memory_session.commit()

    request = RegenerateRequest(
        scope=Scope.SCENE,
        target_stage=StageName.STORY_UNDERSTANDING,
        target_id="scene_01",
        instruction_delta="Mira asks the ferryman for help instead.",
    )

    with (
        patch("daastaan_api.routers.stories.enforce_rate_limit"),
        patch("daastaan_api.routers.stories.enforce_budget"),
        patch("daastaan_api.routers.stories.audit"),
        patch(
            "daastaan_api.routers.stories.dispatch_regeneration", return_value="queued"
        ) as dispatch,
    ):
        accepted = regenerate(request, story, in_memory_session, user)

    child = in_memory_session.get(StoryVersion, accepted.version_id)
    assert child is not None
    assert child.parent_version_id == parent.id
    assert child.version_number == 2
    assert story.current_version_id == child.id
    assert story.status == StoryStatus.GENERATING
    child_state = StoryState.model_validate(child.state_json)
    assert child_state.final_episode_key is None
    assert child_state.final_video_key is None
    assert accepted.stages == [stage.value for stage in PIPELINE_STAGES[1:]]
    dispatch.assert_called_once_with(
        story_id=story.id,
        version_id=child.id,
        user_id=user.id,
        stages=[stage.value for stage in PIPELINE_STAGES[1:]],
        scope="scene",
        target_id="scene_01",
        instruction_delta="Mira asks the ferryman for help instead.",
    )


def test_scene_branch_can_fork_a_historic_version_as_a_sibling(in_memory_session):
    user = User(email="listener@example.test", password_hash="not-used-in-this-test")  # noqa: S106
    in_memory_session.add(user)
    in_memory_session.flush()
    story = Story(user_id=user.id, status=StoryStatus.READY)
    in_memory_session.add(story)
    in_memory_session.flush()
    original = _add_version(in_memory_session, story, _state_for(story, user), 1)
    current = _add_version(
        in_memory_session,
        story,
        _state_for(story, user),
        2,
        parent_id=original.id,
    )
    story.current_version_id = current.id
    in_memory_session.commit()

    request = RegenerateRequest(
        scope=Scope.SCENE,
        target_stage=StageName.STORY_UNDERSTANDING,
        target_id="scene_01",
        instruction_delta="Mira asks the ferryman for help instead.",
        base_version_id=original.id,
        expected_current_version_id=current.id,
    )
    with _regen_patches() as dispatch:
        accepted = regenerate(request, story, in_memory_session, user)

    sibling = in_memory_session.get(StoryVersion, accepted.version_id)
    assert sibling is not None
    assert sibling.parent_version_id == original.id
    assert sibling.version_number == 3
    assert story.current_version_id == sibling.id
    dispatch.assert_called_once()


def test_scene_branch_rejects_foreign_base_version(in_memory_session):
    user = User(email="listener@example.test", password_hash="not-used-in-this-test")  # noqa: S106
    in_memory_session.add(user)
    in_memory_session.flush()
    story = Story(user_id=user.id, status=StoryStatus.READY)
    foreign_story = Story(user_id=user.id, status=StoryStatus.READY)
    in_memory_session.add(story)
    in_memory_session.add(foreign_story)
    in_memory_session.flush()
    current = _add_version(in_memory_session, story, _state_for(story, user), 1)
    foreign = _add_version(in_memory_session, foreign_story, _state_for(foreign_story, user), 1)
    story.current_version_id = current.id
    in_memory_session.commit()

    request = RegenerateRequest(
        scope=Scope.SCENE,
        target_stage=StageName.STORY_UNDERSTANDING,
        target_id="scene_01",
        instruction_delta="Use another path.",
        base_version_id=foreign.id,
        expected_current_version_id=current.id,
    )
    with _regen_patches():
        with pytest.raises(HTTPException) as exc_info:
            regenerate(request, story, in_memory_session, user)
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("status", "expected_current", "expected_code"),
    [
        (StoryStatus.READY, "stale-version", 409),
        (StoryStatus.GENERATING, None, 409),
    ],
)
def test_scene_branch_rejects_stale_or_concurrent_current_version(
    in_memory_session, status, expected_current, expected_code
):
    user = User(email="listener@example.test", password_hash="not-used-in-this-test")  # noqa: S106
    in_memory_session.add(user)
    in_memory_session.flush()
    story = Story(user_id=user.id, status=status)
    in_memory_session.add(story)
    in_memory_session.flush()
    parent = _add_version(in_memory_session, story, _state_for(story, user), 1)
    story.current_version_id = parent.id
    in_memory_session.commit()

    request = RegenerateRequest(
        scope=Scope.SCENE,
        target_stage=StageName.STORY_UNDERSTANDING,
        target_id="scene_01",
        instruction_delta="Use another path.",
        expected_current_version_id=expected_current,
    )
    with _regen_patches() as dispatch:
        with pytest.raises(HTTPException) as exc_info:
            regenerate(request, story, in_memory_session, user)
    assert exc_info.value.status_code == expected_code
    versions = list(
        in_memory_session.exec(
            select(StoryVersion).where(StoryVersion.story_id == story.id)
        ).all()
    )
    assert [version.id for version in versions] == [parent.id]
    dispatch.assert_not_called()


def test_stale_worker_cannot_overwrite_current_branch_status(in_memory_session):
    user = User(email="listener@example.test", password_hash="not-used-in-this-test")  # noqa: S106
    in_memory_session.add(user)
    in_memory_session.flush()
    story = Story(user_id=user.id, status=StoryStatus.GENERATING)
    in_memory_session.add(story)
    in_memory_session.flush()
    old_version = _add_version(in_memory_session, story, _state_for(story, user), 1)
    current = _add_version(in_memory_session, story, _state_for(story, user), 2)
    story.current_version_id = current.id
    in_memory_session.commit()

    _mark_story(in_memory_session, story.id, old_version.id, StoryStatus.READY)
    in_memory_session.refresh(story)
    assert story.current_version_id == current.id
    assert story.status == StoryStatus.GENERATING
