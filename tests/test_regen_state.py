"""The regeneration directive as it is actually written into `state_json`.

The API and the feedback interpreter both stamp a forked version with a plain
dict, which `StoryState` then has to parse back into a `ValidatedDirective`. That
round trip was dead code for a while - the writes never reached the database, so
nothing ever exercised it - and three separate behaviours depend on it: narrowing
the progress rail, appending the user's note to prompts, and bypassing the
response cache so a respeak is not served the take it is replacing.
"""

import pytest
from daastaan_contracts import PIPELINE_STAGES, Scope, StageName, StoryState, plan_stages

# Written verbatim by `stories.regenerate` and `tasks.interpret_feedback`.
DIRECTIVE = {
    "scope": "line",
    "target_stage": "tts_synthesis",
    "target_id": "line-3",
    "instruction_delta": "More urgency.",
}

BASE = {"story_id": "s", "version_id": "v", "user_id": "u", "raw_text": "text"}


def _state(**overrides: object) -> StoryState:
    return StoryState.model_validate({**BASE, **overrides})


class TestDirectiveRoundTrip:
    def test_written_shape_parses(self) -> None:
        state = _state(regen=DIRECTIVE)
        assert state.regen is not None
        assert state.regen.scope == "line"
        assert state.regen.target_stage == "tts_synthesis"
        assert state.regen.target_id == "line-3"
        assert state.regen.instruction_delta == "More urgency."

    def test_absent_target_is_allowed_for_a_full_story_regen(self) -> None:
        state = _state(
            regen={
                "scope": "full_story",
                "target_stage": "story_understanding",
                "target_id": None,
                "instruction_delta": "",
            }
        )
        assert state.regen is not None
        assert state.regen.target_id is None

    def test_a_first_generation_has_no_directive(self) -> None:
        # `create_all` writes the key as JSON null rather than omitting it.
        assert _state(regen=None).regen is None
        assert _state().regen is None

    def test_unknown_directive_keys_are_rejected(self) -> None:
        # The directive originates from a model. A field we do not recognise means
        # the contract has drifted, and silently dropping it would hide that.
        with pytest.raises(ValueError):
            _state(regen={**DIRECTIVE, "model_choice": "gpt-4o"})


class TestDerivedBehaviour:
    def test_presence_of_a_directive_is_what_bypasses_the_cache(self) -> None:
        assert _state(regen=DIRECTIVE).regen is not None
        assert _state(regen=None).regen is None

    def test_directive_narrows_the_reported_stage_list(self) -> None:
        state = _state(regen=DIRECTIVE)
        assert state.regen is not None
        planned = plan_stages(
            Scope(state.regen.scope), StageName(state.regen.target_stage)
        )
        assert planned == (StageName.TTS_SYNTHESIS, StageName.ASSEMBLY)
        assert len(planned) < len(PIPELINE_STAGES)

    def test_instruction_delta_survives_to_where_prompts_read_it(self) -> None:
        state = _state(regen=DIRECTIVE)
        assert state.regen is not None
        assert state.regen.instruction_delta in f"note: {state.regen.instruction_delta}"
