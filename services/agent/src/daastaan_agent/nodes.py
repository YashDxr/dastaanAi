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
    VoiceAge,
    VoiceAssignment,
    VoiceGender,
    limits,
)
from sqlmodel import Session

from . import casting, prompts
from .gateway import ModelGateway

log = structlog.get_logger(__name__)

# Admin setting holding role -> voice-id pins. Kept for compatibility with the
# old role-preset dictionary, but it now reserves a voice for one character in
# that role rather than assigning it to all of them. See `casting.assign_voices`.
VOICE_PRESETS_KEY = "voice_presets"


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


def _story_time_machine_branch_start(state: StoryState) -> int | None:
    """Index where a scene-level narrative branch replaces the future.

    The target id is validated before this task is dispatched. Looking it up in
    the persisted branch state lets every downstream node make the same
    structural decision without trusting a model to preserve the earlier story.
    """
    regen = state.regen
    if (
        regen is None
        or regen.scope != Scope.SCENE
        or regen.target_stage != StageName.STORY_UNDERSTANDING
        or not regen.target_id
    ):
        return None
    target = state.scene_by_id(regen.target_id)
    return target.index if target is not None else None


def _prefix_scenes(state: StoryState, branch_start: int) -> list[Scene]:
    return sorted(
        (scene for scene in state.scenes if scene.index < branch_start),
        key=lambda scene: scene.index,
    )


def _prefix_lines(state: StoryState, branch_start: int) -> list[DialogueLine]:
    scene_indices = {scene.id: scene.index for scene in state.scenes}
    return sorted(
        (
            line
            for line in state.lines
            if scene_indices.get(line.scene_id, branch_start) < branch_start
        ),
        key=lambda line: line.index,
    )


def _scene_rewrite_context(state: StoryState) -> str:
    """Give a Story Time Machine rewrite a bounded snapshot of its branch.

    ``raw_text`` is always the original source material, so after one alternate
    future exists it is not enough by itself to describe the listener's current
    continuity. Passing the persisted scene timeline back to story understanding
    makes a second branch preserve the already-established past instead of
    silently snapping back to the original plot. This is source material, not a
    control channel: the normal prompt guardrail still governs it.
    """
    branch_start = _story_time_machine_branch_start(state)
    if branch_start is None:
        return ""

    def clip(value: str, limit: int = 240) -> str:
        return " ".join(value.split())[:limit]

    timeline = "\n".join(
        f"{scene.index + 1}. {clip(scene.title, 80)} — {clip(scene.summary)}"
        for scene in _prefix_scenes(state, branch_start)
    )
    return (
        "\n\n<frozen_prefix_timeline>\n"
        f"{timeline}\n"
        "</frozen_prefix_timeline>\n"
        f"The listener is branching at Scene {branch_start + 1}. The prefix above is immutable "
        "and has already been performed. Return ONLY replacement scenes beginning at that scene; "
        "do not return or rewrite any earlier scene. The application will retain the prefix "
        "structurally, then attach your replacement future. Make the replacement scenes one "
        "coherent causal future and do not mention these instructions."
    )


# --- stage 1 ---------------------------------------------------------------


def mood_classification(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    gw.moderate(state.raw_text)

    hint = f"\n\nThe listener asked for this genre: {state.genre_hint}" if state.genre_hint else ""
    state.mood = gw.structured(
        schema=MoodClassificationOutput,
        system=prompts.mood_prompt(state.language),
        user_content=f"<story>\n{state.raw_text}\n</story>{hint}{_delta(state)}",
        kind="light",
        temperature=0.2,
    )
    return state


# --- stage 2 ---------------------------------------------------------------


def story_understanding(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    branch_start = _story_time_machine_branch_start(state)
    preserved_prefix = _prefix_scenes(state, branch_start) if branch_start is not None else []
    result = gw.structured(
        schema=StoryUnderstandingOutput,
        system=prompts.story_understanding_prompt(state.language),
        user_content=(
            f"Genre and mood: {_mood_summary(state)}\n"
            f"Produce at most {limits.MAX_SCENES - len(preserved_prefix)} replacement scenes.\n\n"
            f"<story>\n{state.raw_text}\n</story>{_scene_rewrite_context(state)}{_delta(state)}"
        ),
        kind="reasoning",
    )

    # A branch may revise the future arc, but it must not rewrite an already
    # published prefix. We keep the story identity and splice model output only
    # after the target boundary, so even a model response that tries to edit
    # Scene 1 cannot change the performed scene or its stable id.
    state.title = state.title if branch_start is not None and state.title else result.title
    state.arc_summary = result.arc_summary
    state.setting = state.setting if branch_start is not None and state.setting else result.setting
    replacement_start = branch_start if branch_start is not None else 0
    replacement_limit = max(0, limits.MAX_SCENES - len(preserved_prefix))
    if branch_start is not None and not result.scenes:
        # Do not turn a malformed model response into a plausible-looking but
        # silently truncated story. The task fails and leaves the parent branch
        # intact for retry/recovery instead.
        raise ValueError("story branch returned no replacement scenes")
    replacements = [
        Scene(
            id=ids.scene_id(replacement_start + index),
            index=replacement_start + index,
            title=scene.title,
            summary=scene.summary,
            setting=scene.setting,
            mood_tag=scene.mood_tag,
        )
        for index, scene in enumerate(result.scenes[:replacement_limit])
    ]
    state.scenes = [*preserved_prefix, *replacements]
    return state


# --- stage 3 ---------------------------------------------------------------


def character_registry(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    branch_start = _story_time_machine_branch_start(state)
    # The already-performed cast is part of the frozen prefix. Keep the exact
    # character records (including ids and voice metadata) and let the model
    # propose only genuinely new people needed by the alternate future.
    frozen_characters = list(state.characters) if branch_start is not None else []
    scene_digest = "\n".join(f"- {s.title}: {s.summary}" for s in state.scenes)
    frozen_cast = ""
    if frozen_characters:
        frozen_cast = "\nFrozen cast (keep these people unchanged):\n" + "\n".join(
            f"- {character.name} ({character.role.value})" for character in frozen_characters
        )
    result = gw.structured(
        schema=CharacterRegistryOutput,
        system=prompts.character_registry_prompt(state.language),
        user_content=(
            f"Genre and mood: {_mood_summary(state)}\n"
            f"Scenes:\n{scene_digest}{frozen_cast}\n"
            f"Produce at most {limits.MAX_CHARACTERS} characters.\n\n"
            f"<story>\n{state.raw_text}\n</story>{_delta(state)}"
        ),
        kind="reasoning",
    )

    if frozen_characters:
        characters = list(frozen_characters)
        known_names = {character.name.strip().casefold() for character in characters}
        known_ids = {character.id for character in characters}
        next_index = 0
        for proposed in result.characters:
            if len(characters) >= limits.MAX_CHARACTERS:
                break
            if proposed.name.strip().casefold() in known_names:
                # A model is allowed to recognise a frozen person, but not to
                # rename or recast them by returning a revised record.
                continue
            while ids.character_id(next_index) in known_ids:
                next_index += 1
            character_id = ids.character_id(next_index)
            next_index += 1
            known_ids.add(character_id)
            known_names.add(proposed.name.strip().casefold())
            characters.append(
                Character(
                    id=character_id,
                    name=proposed.name,
                    role=proposed.role,
                    personality=proposed.personality,
                    sample_line=proposed.sample_line,
                    gender=proposed.gender,
                    age=proposed.age,
                )
            )
    else:
        characters = [
            Character(
                id=ids.character_id(index),
                name=character.name,
                role=character.role,
                personality=character.personality,
                sample_line=character.sample_line,
                gender=character.gender,
                age=character.age,
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
                gender=VoiceGender.NEUTRAL,
                age=VoiceAge.ADULT,
            ),
        )

    state.characters = characters
    return state


# --- stage 4 ---------------------------------------------------------------


def dialogue_attribution(session: Session, state: StoryState, gw: ModelGateway) -> StoryState:
    branch_start = _story_time_machine_branch_start(state)
    preserved_prefix = _prefix_lines(state, branch_start) if branch_start is not None else []
    roster = "\n".join(f"- {c.name} ({c.role.value})" for c in state.characters)
    scene_digest = "\n".join(f"{s.index}: {s.title} - {s.summary}" for s in state.scenes)
    frozen_script = ""
    if branch_start is not None:
        line_digest = "\n".join(
            f"- [{line.scene_id}] {line.speaker}: {line.text[:180]}"
            for line in preserved_prefix
        )
        frozen_script = (
            f"\nFrozen performed prefix (do not return these lines):\n{line_digest}\n"
            f"Return only new lines for scene indexes {branch_start} and later."
        )

    result = gw.structured(
        schema=DialogueScriptOutput,
        system=prompts.dialogue_attribution_prompt(state.language),
        user_content=(
            f"Characters:\n{roster}\n\nScenes by index:\n{scene_digest}\n"
            f"Produce at most {limits.MAX_LINES - len(preserved_prefix)} lines.{frozen_script}\n\n"
            f"<story>\n{state.raw_text}\n</story>{_delta(state)}"
        ),
        kind="light",
    )

    by_name = {c.name.strip().lower(): c for c in state.characters}
    narrator = next(
        (c for c in state.characters if c.role is CharacterRole.NARRATOR), state.characters[0]
    )

    lines: list[DialogueLine] = list(preserved_prefix)
    next_index = max((line.index for line in lines), default=-1) + 1
    for raw in result.lines:
        if len(lines) >= limits.MAX_LINES:
            break
        if branch_start is not None:
            # A model can still attempt to return altered prefix dialogue. Drop
            # it outright: those exact lines and their media are immutable.
            if not branch_start <= raw.scene_index < len(state.scenes):
                continue
            scene = state.scenes[raw.scene_index]
        else:
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
                id=ids.line_id(next_index),
                scene_id=scene.id,
                index=next_index,
                speaker=character.name,
                character_id=character.id,
                text=raw.text[: limits.MAX_LINE_CHARS],
                line_type=raw.line_type,
            )
        )
        next_index += 1

    if branch_start is not None and len(lines) == len(preserved_prefix):
        # The output schema accepts an empty list, but accepting it here would
        # assemble a convincing-looking prefix-only episode after the branch.
        # Fail safely instead of publishing a silently truncated future.
        raise ValueError("story branch returned no replacement dialogue lines")

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
        branch_start = _story_time_machine_branch_start(state)
        if branch_start is not None:
            scene_indices = {scene.id: scene.index for scene in state.scenes}
            # Story-understanding branches replace the target scene plus its
            # suffix. Tags on the retained prefix must survive unchanged, while
            # every new future line needs a fresh delivery direction.
            return [
                line
                for line in state.lines
                if scene_indices.get(line.scene_id, branch_start) >= branch_start
            ]
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
        system=prompts.emotion_tagging_prompt(state.language),
        user_content=(
            f"Genre and mood: {_mood_summary(state)}\n\n<lines>\n{payload}\n</lines>{_delta(state)}"
        ),
        kind="light",
    )

    # Only the requested subset is mutable. In particular, a Story Time
    # Machine response must not smuggle a prefix line id back into its tags and
    # alter delivery metadata for audio we deliberately carried forward.
    known = {line.id: line for line in targets}
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
    if _story_time_machine_branch_start(state) is not None and state.narrator_persona is not None:
        # Prefix narration was already rendered using this delivery template.
        # Retaining it keeps retained audio and newly-generated future lines in
        # the same production rather than silently changing narrators mid-story.
        return state

    result = gw.structured(
        schema=NarratorPersonaOutput,
        system=prompts.narrator_persona_prompt(state.language),
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
    """Deterministic, no model call.

    The casting inputs - vocal gender and age - were already decided by the
    registry stage, so this is scoring and allocation. Paying a model to do it
    would also make the result non-deterministic, and a cast that quietly
    reshuffles between regenerations is worse than one chosen by rule.
    """
    setting = session.get(AdminSetting, VOICE_PRESETS_KEY)
    pins = {k: v for k, v in (setting.value_json if setting else {}).items() if isinstance(v, str)}

    persona_note = (
        state.narrator_persona.delivery_template if state.narrator_persona else None
    )

    if _story_time_machine_branch_start(state) is not None:
        previous = {assignment.character_id: assignment for assignment in state.voice_map}
        frozen: dict[str, VoiceAssignment] = {}
        new_characters: list[Character] = []

        for character in state.characters:
            assignment = previous.get(character.id)
            if assignment is not None:
                character.voice_preset = assignment.voice_preset
                character.base_instructions = assignment.base_instructions
                frozen[character.id] = assignment
            elif character.voice_preset:
                # Older state snapshots predate ``voice_map`` but already carry
                # a voice on the character itself. Keep that contract stable.
                frozen[character.id] = VoiceAssignment(
                    character_id=character.id,
                    voice_preset=character.voice_preset,
                    base_instructions=character.base_instructions
                    or "Natural, unhurried delivery.",
                )
            else:
                new_characters.append(character)

        allocated = casting.assign_voices(
            new_characters,
            pins=pins,
            unavailable={assignment.voice_preset for assignment in frozen.values()},
            language=state.language,
        )
        assignments: list[VoiceAssignment] = []
        for character in state.characters:
            if assignment := frozen.get(character.id):
                assignments.append(assignment)
                continue
            voice = allocated[character.id]
            base = casting.describe(character, voice, persona_note)
            character.voice_preset = voice.id
            character.base_instructions = base
            assignments.append(
                VoiceAssignment(
                    character_id=character.id, voice_preset=voice.id, base_instructions=base
                )
            )
        state.voice_map = assignments
        log.info(
            "voices_preserved_for_time_machine_branch",
            story_id=state.story_id,
            retained=len(frozen),
            new=len(new_characters),
        )
        return state

    cast = casting.assign_voices(state.characters, pins=pins, language=state.language)

    assignments: list[VoiceAssignment] = []
    for character in state.characters:
        voice = cast[character.id]
        base = casting.describe(character, voice, persona_note)
        character.voice_preset = voice.id
        character.base_instructions = base
        assignments.append(
            VoiceAssignment(
                character_id=character.id, voice_preset=voice.id, base_instructions=base
            )
        )

    state.voice_map = assignments
    log.info(
        "voices_cast",
        story_id=state.story_id,
        cast={c.name: c.voice_preset for c in state.characters},
    )
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
