"""Domain models shared by the API, the agent service, and the frontends.

Models suffixed `Output` are used directly as OpenAI structured-output schemas.
Keep those free of default values and exotic JSON Schema constraints, because
strict mode requires every property to be required and supports only a narrow
subset of keywords. Range checks belong in validators, which run after parsing.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import AssetKind, CharacterRole, LineType, VoiceAge, VoiceGender

# Identifiers are minted by our code, never by a model. Stages that need to refer
# back to an entity echo one of these strings, and we reject anything unrecognised.
SceneId = str
CharacterId = str
LineId = str


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Document ingest -------------------------------------------------------


class StoryCleanupOutput(Strict):
    """Result of turning an uploaded document into something the pipeline can
    read. Runs before stage 1, on text that came out of a parser or an OCR
    engine, so the model is doing two jobs: discarding non-story matter and
    repairing whatever the extraction mangled."""

    cleaned_text: str
    title_hint: str
    genre_hint: str
    notes: str


# --- Stage 1: mood / genre -------------------------------------------------


class MoodClassificationOutput(Strict):
    genre: str
    mood: str
    tone_keywords: list[str]
    pacing: str


# --- Stage 2: story understanding ------------------------------------------


class SceneOutput(Strict):
    title: str
    summary: str
    setting: str
    mood_tag: str


class StoryUnderstandingOutput(Strict):
    title: str
    arc_summary: str
    setting: str
    scenes: list[SceneOutput]


class Scene(Strict):
    id: SceneId
    index: int
    title: str
    summary: str
    setting: str
    mood_tag: str


# --- Stage 3: character registry -------------------------------------------


class CharacterOutput(Strict):
    name: str
    role: CharacterRole
    personality: str
    sample_line: str
    # Casting inputs. Without these the voice allocator has nothing to match on
    # but narrative role, which is how six characters ended up sharing a couple
    # of timbres and differing only in delivery.
    gender: VoiceGender
    age: VoiceAge


class CharacterRegistryOutput(Strict):
    characters: list[CharacterOutput]


class Character(Strict):
    id: CharacterId
    name: str
    role: CharacterRole
    personality: str
    sample_line: str
    # Defaulted because stories generated before casting existed are replayed
    # through `StoryState.model_validate` on every regeneration.
    gender: VoiceGender = VoiceGender.NEUTRAL
    age: VoiceAge = VoiceAge.ADULT
    voice_preset: str | None = None
    base_instructions: str | None = None


# --- Stage 4: dialogue attribution -----------------------------------------


class DialogueLineOutput(Strict):
    scene_index: int
    speaker: str
    text: str
    line_type: LineType


class DialogueScriptOutput(Strict):
    lines: list[DialogueLineOutput]


class DialogueLine(Strict):
    id: LineId
    scene_id: SceneId
    index: int
    speaker: str
    character_id: CharacterId | None = None
    text: str
    line_type: LineType
    emotion: str | None = None
    intensity: int | None = None
    tts_instructions: str | None = None
    pause_after_ms: int = 0


class ConsistencyFindingOutput(Strict):
    """One issue found by the on-demand Plot Hole Hunter model.

    The model refers to a scene by its stable *index*, not its database id. The
    worker maps that index back to a real scene id and drops references it cannot
    verify before anything reaches a client.
    """

    severity: Literal["critical", "warning"]
    type: Literal["knowledge", "timeline", "character", "causality", "continuity"]
    scene_index: int
    line_id: str | None
    explanation: str
    suggestion: str


class ConsistencyAnalysisOutput(Strict):
    """Strict structured output for the on-demand consistency checker.

    This is deliberately not a pipeline stage. It is a read-only editorial
    review that can be requested for a completed story without altering the
    version, its media, or its regeneration plan.
    """

    summary: str
    findings: list[ConsistencyFindingOutput]


class ConsistencyFinding(Strict):
    """A checked, user-safe finding persisted by the worker and returned by API."""

    severity: Literal["critical", "warning"]
    type: Literal["knowledge", "timeline", "character", "causality", "continuity"]
    scene_id: SceneId
    line_id: LineId | None
    explanation: str
    suggestion: str


# --- Stage 5: emotion tagging ----------------------------------------------


class EmotionTagOutput(Strict):
    line_id: LineId
    emotion: str
    intensity: int
    tts_instructions: str
    pause_after_ms: int

    @field_validator("intensity")
    @classmethod
    def _intensity_in_range(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("intensity must be between 1 and 5")
        return v

    @field_validator("pause_after_ms")
    @classmethod
    def _pause_sane(cls, v: int) -> int:
        if not 0 <= v <= 5000:
            raise ValueError("pause_after_ms must be between 0 and 5000")
        return v


class EmotionTaggingOutput(Strict):
    tags: list[EmotionTagOutput]


# --- Stage 6: narrator persona ---------------------------------------------


class NarratorPersonaOutput(Strict):
    persona_name: str
    tone: str
    pacing: str
    style_notes: str
    delivery_template: str


class NarratorPersona(NarratorPersonaOutput):
    pass


# --- Stage 7: voice assignment ---------------------------------------------


class VoiceAssignment(Strict):
    character_id: CharacterId
    voice_preset: str
    base_instructions: str


# --- Media -----------------------------------------------------------------


class MediaAsset(Strict):
    id: str
    kind: AssetKind
    object_key: str
    duration_ms: int | None = None
    line_id: LineId | None = None
    scene_id: SceneId | None = None
    content_type: str = "audio/mpeg"


# --- Regeneration ----------------------------------------------------------


class RegenDirective(Strict):
    """Output of the Feedback Interpreter.

    This is the highest-risk model in the system: free user text becomes a
    directive that changes what the backend executes. `target_stage` and `scope`
    are enums so an injected value cannot name an arbitrary stage, and
    `instruction_delta` is capped flavour text that only ever reaches a content
    prompt, never a model selector, a billing target, or a shell argument.
    """

    scope: str
    target_stage: str
    target_id: str | None
    instruction_delta: str
    reason: str

    @field_validator("instruction_delta", "reason")
    @classmethod
    def _cap_text(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError("text field exceeds 500 characters")
        return v


class ValidatedDirective(Strict):
    """`RegenDirective` after server-side validation against the stage registry
    and an ownership check. Only this type is allowed to reach the dispatcher."""

    scope: str
    target_stage: str
    target_id: str | None
    instruction_delta: str = Field(default="", max_length=500)


# --- Writers Room ----------------------------------------------------------


class PersonaCritique(Strict):
    persona: str
    strengths: list[str]
    concerns: list[str]
    suggestions: list[str]


class RevisionBrief(Strict):
    summary: str
    key_themes: list[str]
    priority_actions: list[str]
    overall_score: int  # 1-10

    @field_validator("overall_score")
    @classmethod
    def _score_in_range(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError("overall_score must be between 1 and 10")
        return v


class WritersRoomResult(Strict):
    critiques: list[PersonaCritique]
    brief: RevisionBrief


# --- Cliffhanger Optimizer ------------------------------------------------


class EndingSuggestion(Strict):
    title: str
    sketch: str           # 2-3 sentence alternative ending
    tension_score: int    # 1-10
    binge_probability: int  # 1-100

    @field_validator("tension_score")
    @classmethod
    def _tension_in_range(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError("tension_score must be between 1 and 10")
        return v

    @field_validator("binge_probability")
    @classmethod
    def _binge_in_range(cls, v: int) -> int:
        if not 1 <= v <= 100:
            raise ValueError("binge_probability must be between 1 and 100")
        return v


class CliffhangerResult(Strict):
    current_score: int        # 1-10
    current_analysis: str
    tension: int              # 1-10
    unresolved_threads: list[str]
    suggestions: list[EndingSuggestion]

    @field_validator("current_score", "tension")
    @classmethod
    def _score_in_range_cliff(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError("score must be between 1 and 10")
        return v


# --- Story Genome ----------------------------------------------------------


class GenomeTrait(Strict):
    trait: str          # e.g. "slow-burn tension"
    value: float        # 0.0-1.0
    explanation: str

    @field_validator("value")
    @classmethod
    def _value_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("value must be between 0.0 and 1.0")
        return v


class StoryConcept(Strict):
    title: str
    premise: str
    why_similar: str


# The docstring below is sent to the model as the schema description, so the
# reasoning for the split lives out here instead. `dialogue_ratio` and
# `character_balance` are counted from `StoryState` and are deliberately absent:
# beyond preferring arithmetic to a language model, `character_balance` is a
# name-keyed map, and structured outputs cannot express an open-ended object —
# asking for it fails the entire request rather than just that one field.
class StoryGenomeSynthesis(Strict):
    """The judged half of a story's DNA: its traits, arc shape, pacing, and the
    concepts that share its emotional signature."""

    traits: list[GenomeTrait]
    arc_shape: str              # "classic three-act", "in medias res", etc.
    pacing_profile: str
    concepts: list[StoryConcept]
    summary: str


class StoryGenomeResult(StoryGenomeSynthesis):
    """The synthesis joined with the metrics we computed ourselves."""

    dialogue_ratio: float
    character_balance: dict[str, float]  # character name -> % of lines
