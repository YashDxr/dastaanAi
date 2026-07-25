"""The stage registry.

A full run is the whole ordered list. A scoped regeneration is a contiguous slice
of the same list starting at the target stage, so both paths execute identical
code and there is no second orchestration flow to debug.
"""

from .enums import Scope, StageName

# Sequential, LLM-bound. One Celery task each, so a single stage can be retried
# without redoing the ones before it.
AGENT_STAGES: tuple[StageName, ...] = (
    StageName.MOOD_CLASSIFICATION,
    StageName.STORY_UNDERSTANDING,
    StageName.CHARACTER_REGISTRY,
    StageName.DIALOGUE_ATTRIBUTION,
    StageName.EMOTION_TAGGING,
    StageName.NARRATOR_PERSONA,
    StageName.VOICE_ASSIGNMENT,
)

# Expanded into a Celery group: one subtask per line or per scene.
FANOUT_STAGES: tuple[StageName, ...] = (
    StageName.TTS_SYNTHESIS,
    StageName.IMAGE_GENERATION,
)

# Music is a single, long-running media task rather than a per-line/scene
# fan-out, but it still belongs in the ordered pipeline and can be regenerated
# independently of narration and artwork.
PIPELINE_STAGES: tuple[StageName, ...] = (
    *AGENT_STAGES,
    *FANOUT_STAGES,
    StageName.MUSIC_GENERATION,
    StageName.ASSEMBLY,
)

_STAGE_INDEX: dict[StageName, int] = {stage: i for i, stage in enumerate(PIPELINE_STAGES)}

# Which stages a scoped regeneration is allowed to re-enter at. Anything outside
# this map is rejected before dispatch.
SCOPE_ENTRY_POINTS: dict[Scope, frozenset[StageName]] = {
    Scope.LINE: frozenset({StageName.EMOTION_TAGGING, StageName.TTS_SYNTHESIS}),
    Scope.CHARACTER: frozenset({StageName.VOICE_ASSIGNMENT, StageName.TTS_SYNTHESIS}),
    Scope.SCENE: frozenset({StageName.IMAGE_GENERATION, StageName.STORY_UNDERSTANDING}),
    Scope.MUSIC: frozenset({StageName.MUSIC_GENERATION}),
    Scope.FULL_STORY: frozenset(PIPELINE_STAGES),
}

# Everything downstream of an entry point is *eligible* to re-run, but a narrow
# scope should not drag the whole tail along with it: re-tagging one line must not
# regenerate every scene image. Intersecting the downstream slice with this map is
# what keeps a scoped regeneration cheap enough to do live on stage.
SCOPE_AFFECTED_STAGES: dict[Scope, frozenset[StageName]] = {
    Scope.LINE: frozenset(
        {StageName.EMOTION_TAGGING, StageName.TTS_SYNTHESIS, StageName.ASSEMBLY}
    ),
    Scope.CHARACTER: frozenset(
        {StageName.VOICE_ASSIGNMENT, StageName.TTS_SYNTHESIS, StageName.ASSEMBLY}
    ),
    # A scene rewrite genuinely cascades, so it keeps the full downstream slice.
    Scope.SCENE: frozenset(PIPELINE_STAGES),
    Scope.MUSIC: frozenset({StageName.MUSIC_GENERATION, StageName.ASSEMBLY}),
    Scope.FULL_STORY: frozenset(PIPELINE_STAGES),
}


def stage_index(stage: StageName) -> int:
    return _STAGE_INDEX[stage]


def is_fanout(stage: StageName) -> bool:
    return stage in FANOUT_STAGES


def downstream_from(stage: StageName) -> tuple[StageName, ...]:
    """The stage itself plus everything after it."""
    return PIPELINE_STAGES[_STAGE_INDEX[stage] :]


def is_valid_entry(scope: Scope, stage: StageName) -> bool:
    return stage in SCOPE_ENTRY_POINTS.get(scope, frozenset())


def plan_stages(scope: Scope, target_stage: StageName) -> tuple[StageName, ...]:
    """Stages to execute for a regeneration.

    The downstream slice bounds what *may* re-run and the scope filter bounds what
    actually does. Assembly is always included, because any regeneration changes
    an asset and the episode has to be recomposed even when a single line changed.
    """
    if not is_valid_entry(scope, target_stage):
        raise ValueError(f"stage {target_stage!r} is not a valid entry point for scope {scope!r}")
    affected = SCOPE_AFFECTED_STAGES[scope]
    planned = tuple(stage for stage in downstream_from(target_stage) if stage in affected)
    if StageName.ASSEMBLY not in planned:
        planned = (*planned, StageName.ASSEMBLY)
    return planned
