"""The reasoning stages.

Each node has the same shape - `(session, state, gateway) -> StoryState` - and is
registered in `STAGE_NODES`. The Celery chain and the LangGraph build both read
that registry, so a full run and a scoped regeneration execute the same code.

Nodes have no tool access, no database writes of their own, and no say in what
runs next. They return data; the orchestration layer decides what to do with it.
That containment is why an injection in a node can only corrupt story content.
"""

import json
from collections.abc import Callable

import structlog
from daastaan_common import ids
from daastaan_common.models import AdminSetting
from daastaan_contracts import (
    Character,
    CharacterRegistryOutput,
    CharacterRole,
    DialogueLine,
    DialogueScriptOutput,
    EmotionTaggingOutput,
    LineType,
    MoodClassificationOutput,
    NarratorPersona,
    NarratorPersonaOutput,
    Scene,
    Scope,
    StageName,
    StoryState,
    StoryUnderstandingOutput,
    VoiceAssignment,
    limits,
)
from sqlmodel import Session

from . import prompts
from .gateway import ModelGateway

log = structlog.get_logger(__name__)

VOICE_PRESETS_KEY = "voice_presets"

# OpenAI TTS voices, mapped by narrative role. Overridable from the admin panel.
DEFAULT_VOICE_PRESETS: dict[str, str] = {
    CharacterRole.NARRATOR.value: "sage",
    CharacterRole.PROTAGONIST.value: "nova",
    CharacterRole.ANTAGONIST.value: "onyx",
    CharacterRole.SUPPORTING.value: "coral",
}
SUPPORTING_ROTATION = ("coral", "ballad", "verse", "echo", "fable")


def _mood_summary(state: StoryState) -> str:
    if not state.mood:
        return "unspecified"
    return f"{state.mood.genre} / {state.mood.mood} / pacing {state.mood.pacing}"


def _delta(state: StoryState) -> str:
    """The user's creative note on a regeneration, if any. Treated purely as
    flavour text appended to the content payload."""
    if state.regen and state.regen.instruction_delta:
        return f"\n\nAdditional direction from the listener: {state.regen.instruction_delta}"
    return ""


# --- stage 1 ---------------------------------------------------------------


def mood_classification(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    gw.moderate(state.raw_text)

    hint = f"\n\nThe listener asked for this genre: {state.genre_hint}" if state.genre_hint else ""
    state.mood = gw.structured(
        schema=MoodClassificationOutput,
        system=prompts.MOOD,
        user_content=f"<story>\n{state.raw_text}\n</story>{hint}{_delta(state)}",
        kind="light",
        temperature=0.2,
    )
    return state


# --- stage 2 ---------------------------------------------------------------


def story_understanding(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    result = gw.structured(
        schema=StoryUnderstandingOutput,
        system=prompts.STORY_UNDERSTANDING,
        user_content=(
            f"Genre and mood: {_mood_summary(state)}\n"
            f"Produce at most {limits.MAX_SCENES} scenes.\n\n"
            f"<story>\n{state.raw_text}\n</story>{_delta(state)}"
        ),
        kind="reasoning",
    )

    state.title = result.title
    state.arc_summary = result.arc_summary
    state.setting = result.setting
    state.scenes = [
        Scene(
            id=ids.scene_id(index),
            index=index,
            title=scene.title,
            summary=scene.summary,
            setting=scene.setting,
            mood_tag=scene.mood_tag,
        )
        for index, scene in enumerate(result.scenes[: limits.MAX_SCENES])
    ]
    return state


# --- stage 3 ---------------------------------------------------------------


def character_registry(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    scene_digest = "\n".join(f"- {s.title}: {s.summary}" for s in state.scenes)
    result = gw.structured(
        schema=CharacterRegistryOutput,
        system=prompts.CHARACTER_REGISTRY,
        user_content=(
            f"Genre and mood: {_mood_summary(state)}\n"
            f"Scenes:\n{scene_digest}\n"
            f"Produce at most {limits.MAX_CHARACTERS} characters.\n\n"
            f"<story>\n{state.raw_text}\n</story>{_delta(state)}"
        ),
        kind="reasoning",
    )

    characters = [
        Character(
            id=ids.character_id(index),
            name=character.name,
            role=character.role,
            personality=character.personality,
            sample_line=character.sample_line,
        )
        for index, character in enumerate(result.characters[: limits.MAX_CHARACTERS])
    ]
    # A narrator is required downstream, so synthesise one if the model omitted it.
    if not any(c.role is CharacterRole.NARRATOR for c in characters):
        characters.insert(
            0,
            Character(
                id=ids.character_id(len(characters)),
                name="Narrator",
                role=CharacterRole.NARRATOR,
                personality="Measured, observant storyteller.",
                sample_line="And so it began.",
            ),
        )

    state.characters = characters
    return state


# --- stage 4 ---------------------------------------------------------------


def dialogue_attribution(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    roster = "\n".join(f"- {c.name} ({c.role.value})" for c in state.characters)
    scene_digest = "\n".join(f"{s.index}: {s.title} - {s.summary}" for s in state.scenes)

    result = gw.structured(
        schema=DialogueScriptOutput,
        system=prompts.DIALOGUE_ATTRIBUTION,
        user_content=(
            f"Characters:\n{roster}\n\nScenes by index:\n{scene_digest}\n"
            f"Produce at most {limits.MAX_LINES} lines.\n\n"
            f"<story>\n{state.raw_text}\n</story>{_delta(state)}"
        ),
        kind="light",
    )

    by_name = {c.name.strip().lower(): c for c in state.characters}
    narrator = next(
        (c for c in state.characters if c.role is CharacterRole.NARRATOR), state.characters[0]
    )

    lines: list[DialogueLine] = []
    for index, raw in enumerate(result.lines[: limits.MAX_LINES]):
        scene = (
            state.scenes[raw.scene_index]
            if 0 <= raw.scene_index < len(state.scenes)
            else state.scenes[0]
        )
        # An unrecognised speaker falls back to the narrator rather than failing
        # the run: the model occasionally invents a name, and a slightly
        # mis-attributed line beats a dead pipeline.
        character = (
            narrator
            if raw.line_type is LineType.NARRATION
            else by_name.get(raw.speaker.strip().lower(), narrator)
        )
        lines.append(
            DialogueLine(
                id=ids.line_id(index),
                scene_id=scene.id,
                index=index,
                speaker=character.name,
                character_id=character.id,
                text=raw.text[: limits.MAX_LINE_CHARS],
                line_type=raw.line_type,
            )
        )

    state.lines = lines
    return state


# --- stage 5 ---------------------------------------------------------------


def _lines_in_scope(state: StoryState) -> list[DialogueLine]:
    """Narrow the work to what the regeneration actually asked for. This is where
    a line-scoped rerun becomes one cheap call instead of re-tagging the script."""
    regen = state.regen
    if regen is None:
        return state.lines

    if regen.scope == Scope.LINE and regen.target_id:
        return [line for line in state.lines if line.id == regen.target_id]
    if regen.scope == Scope.CHARACTER and regen.target_id:
        return [line for line in state.lines if line.character_id == regen.target_id]
    if regen.scope == Scope.SCENE and regen.target_id:
        return [line for line in state.lines if line.scene_id == regen.target_id]
    return state.lines


def emotion_tagging(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    targets = _lines_in_scope(state)
    if not targets:
        return state

    payload = json.dumps(
        [{"line_id": line.id, "speaker": line.speaker, "text": line.text} for line in targets],
        ensure_ascii=False,
    )
    result = gw.structured(
        schema=EmotionTaggingOutput,
        system=prompts.EMOTION_TAGGING,
        user_content=(
            f"Genre and mood: {_mood_summary(state)}\n\n<lines>\n{payload}\n</lines>{_delta(state)}"
        ),
        kind="light",
    )

    known = {line.id: line for line in state.lines}
    for tag in result.tags:
        # Ignore ids the model invented; only echoes of real lines are applied.
        line = known.get(tag.line_id)
        if line is None:
            log.warning("emotion_tag_unknown_line", line_id=tag.line_id)
            continue
        line.emotion = tag.emotion
        line.intensity = tag.intensity
        line.tts_instructions = tag.tts_instructions
        line.pause_after_ms = tag.pause_after_ms

    return state


# --- stage 6 ---------------------------------------------------------------


def narrator_persona(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    result = gw.structured(
        schema=NarratorPersonaOutput,
        system=prompts.NARRATOR_PERSONA,
        user_content=(
            f"Genre and mood: {_mood_summary(state)}\n"
            f"Arc: {state.arc_summary or 'unknown'}\n"
            f"Setting: {state.setting or 'unknown'}{_delta(state)}"
        ),
        kind="light",
    )
    state.narrator_persona = NarratorPersona(**result.model_dump())
    return state


# --- stage 7 ---------------------------------------------------------------


def voice_assignment(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    """Deterministic, no model call. Role-to-voice mapping is configuration, and
    paying a model to do a dictionary lookup would be silly."""
    setting = session.get(AdminSetting, VOICE_PRESETS_KEY)
    presets = {**DEFAULT_VOICE_PRESETS, **(setting.value_json if setting else {})}

    persona_note = (
        state.narrator_persona.delivery_template if state.narrator_persona else "Natural delivery."
    )

    assignments: list[VoiceAssignment] = []
    supporting_index = 0
    for character in state.characters:
        if character.role is CharacterRole.SUPPORTING:
            voice = SUPPORTING_ROTATION[supporting_index % len(SUPPORTING_ROTATION)]
            supporting_index += 1
        else:
            voice = presets.get(character.role.value, "alloy")

        base = (
            persona_note
            if character.role is CharacterRole.NARRATOR
            else f"Voice of {character.name}. {character.personality}"
        )
        character.voice_preset = voice
        character.base_instructions = base
        assignments.append(
            VoiceAssignment(character_id=character.id, voice_preset=voice, base_instructions=base)
        )

    state.voice_map = assignments
    return state


StageNode = Callable[[Session, StoryState, ModelGateway], StoryState]

STAGE_NODES: dict[StageName, StageNode] = {
    StageName.MOOD_CLASSIFICATION: mood_classification,
    StageName.STORY_UNDERSTANDING: story_understanding,
    StageName.CHARACTER_REGISTRY: character_registry,
    StageName.DIALOGUE_ATTRIBUTION: dialogue_attribution,
    StageName.EMOTION_TAGGING: emotion_tagging,
    StageName.NARRATOR_PERSONA: narrator_persona,
    StageName.VOICE_ASSIGNMENT: voice_assignment,
}
