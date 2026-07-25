"""Domain models shared by the API, the agent service, and the frontends.

Models suffixed `Output` are used directly as OpenAI structured-output schemas.
Keep those free of default values and exotic JSON Schema constraints, because
strict mode requires every property to be required and supports only a narrow
subset of keywords. Range checks belong in validators, which run after parsing.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import AssetKind, CharacterRole, LineType

# Identifiers are minted by our code, never by a model. Stages that need to refer
# back to an entity echo one of these strings, and we reject anything unrecognised.
SceneId = str
CharacterId = str
LineId = str


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class CharacterRegistryOutput(Strict):
    characters: list[CharacterOutput]


class Character(Strict):
    id: CharacterId
    name: str
    role: CharacterRole
    personality: str
    sample_line: str
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
