"""Story Genome: what is counted, and what is asked for.

The split between the two matters twice over. The counted half is exact and a
model's guess at it would not be, and the counted half also contains the one
shape — a map keyed by character name — that structured outputs cannot express.
Asking for it once cost the feature every request it made.
"""

from typing import Any

from daastaan_agent.story_genome import analyze_genome
from daastaan_contracts.models import (
    GenomeTrait,
    StoryConcept,
    StoryGenomeSynthesis,
)

SYNTHESIS = StoryGenomeSynthesis(
    traits=[GenomeTrait(trait="atmospheric", value=0.8, explanation="Heavy on setting.")],
    arc_shape="classic three-act",
    pacing_profile="slow build to a warm close",
    concepts=[StoryConcept(title="The Quiet Door", premise="A child.", why_similar="Wonder.")],
    summary="A gentle discovery story.",
)


class RecordingGateway:
    """Captures the schema it was handed and returns a fixed synthesis."""

    def __init__(self) -> None:
        self.schemas: list[type] = []

    def structured(self, *, schema: type, **_: Any) -> Any:
        self.schemas.append(schema)
        return SYNTHESIS


def test_model_is_never_asked_for_the_counted_metrics(state_fully_staged):
    gateway = RecordingGateway()
    analyze_genome(state_fully_staged, gateway)

    assert gateway.schemas == [StoryGenomeSynthesis]
    asked_for = set(StoryGenomeSynthesis.model_fields)
    assert "character_balance" not in asked_for
    assert "dialogue_ratio" not in asked_for


def test_counted_metrics_come_from_the_state(state_fully_staged):
    """Three lines, one of them narration, one line each from three speakers."""
    result = analyze_genome(state_fully_staged, RecordingGateway())

    assert result.dialogue_ratio == 0.667
    assert result.character_balance == {
        "Narrator": 0.333,
        "Lily": 0.333,
        "Rufus": 0.333,
    }


def test_synthesis_is_carried_through_untouched(state_fully_staged):
    result = analyze_genome(state_fully_staged, RecordingGateway())

    assert result.arc_shape == SYNTHESIS.arc_shape
    assert result.pacing_profile == SYNTHESIS.pacing_profile
    assert result.summary == SYNTHESIS.summary
    assert result.traits == SYNTHESIS.traits
    assert result.concepts == SYNTHESIS.concepts


def test_result_serialises_to_the_shape_the_api_stores(state_fully_staged):
    """`run_story_genome_task` persists this dict and the studio reads it, so the
    keys are a contract with the frontend rather than an internal detail."""
    stored = analyze_genome(state_fully_staged, RecordingGateway()).model_dump()

    assert set(stored) == {
        "traits",
        "arc_shape",
        "pacing_profile",
        "dialogue_ratio",
        "character_balance",
        "concepts",
        "summary",
    }
