"""Contract test for the button-driven alternate-endings experience.

The React component intentionally calls the existing regeneration endpoint rather
than introducing an alternate pipeline. This pins the exact directive shape it
sends: a scene-scoped fork anchored at the penultimate scene and re-entering at
story understanding.
"""

from daastaan_contracts import Scene, Scope, StageName, StoryState, downstream_from, plan_stages


def _story_with_three_scenes() -> StoryState:
    return StoryState(
        story_id="story-1",
        version_id="version-1",
        user_id="user-1",
        raw_text="A sufficiently long source story that can become an audio drama.",
        scenes=[
            Scene(
                id="scene_00",
                index=0,
                title="Beginning",
                summary="The ordinary world.",
                setting="Home",
                mood_tag="calm",
            ),
            Scene(
                id="scene_01",
                index=1,
                title="Turning point",
                summary="A decision changes everything.",
                setting="Crossroads",
                mood_tag="tense",
            ),
            Scene(
                id="scene_02",
                index=2,
                title="Original ending",
                summary="The original resolution.",
                setting="Home",
                mood_tag="warm",
            ),
        ],
    )


def test_alternate_ending_uses_the_penultimate_scene_and_existing_scene_regeneration():
    state = _story_with_three_scenes()
    pivot = sorted(state.scenes, key=lambda scene: scene.index)[-2]
    request = {
        "scope": Scope.SCENE.value,
        "target_stage": StageName.STORY_UNDERSTANDING.value,
        "target_id": pivot.id,
        "instruction_delta": (
            "Create an alternate ending from this scene forward. Preserve the established facts "
            "before this point and resolve the remaining story with a hopeful, earned outcome."
        ),
    }
    forked_state = StoryState.model_validate(
        {
            **state.model_dump(mode="json"),
            "version_id": "version-2",
            "regen": request,
        }
    )

    assert forked_state.regen is not None
    assert forked_state.regen.target_id == "scene_01"
    assert state.scene_by_id(forked_state.regen.target_id) is pivot
    assert plan_stages(
        Scope(forked_state.regen.scope), StageName(forked_state.regen.target_stage)
    ) == downstream_from(StageName.STORY_UNDERSTANDING)
