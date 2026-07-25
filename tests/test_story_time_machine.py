"""The explicit API path used by the Story Time Machine UI.

The browser only chooses a scene and supplies a creative note. The API must keep
owning the target validation, parent/child version fork, and stage plan so a
crafted browser request cannot broaden the pipeline.
"""

from unittest.mock import patch

from daastaan_api.routers.stories import regenerate
from daastaan_api.schemas import RegenerateRequest
from daastaan_common.models import Story, StoryVersion, User
from daastaan_contracts import PIPELINE_STAGES, Scope, StageName, StoryState, StoryStatus


def test_scene_branch_forks_from_current_version_and_rebuilds_future(in_memory_session):
    user = User(email="listener@example.test", password_hash="not-used-in-this-test")  # noqa: S106
    in_memory_session.add(user)
    in_memory_session.flush()

    story = Story(user_id=user.id, status=StoryStatus.READY)
    in_memory_session.add(story)
    in_memory_session.flush()

    state = StoryState.model_validate(
        {
            "story_id": story.id,
            "version_id": "placeholder",
            "user_id": user.id,
            "raw_text": "A story long enough to satisfy the domain contract.",
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
    parent = StoryVersion(
        story_id=story.id,
        version_number=1,
        state_json=state.model_dump(mode="json"),
    )
    in_memory_session.add(parent)
    in_memory_session.flush()
    parent.state_json["version_id"] = parent.id
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
