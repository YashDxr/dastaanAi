"""Version forking.

A regeneration writes a new `StoryVersion` rather than mutating the current one,
so every run is reproducible and the previous mix stays playable. Media assets are
scoped to a version, which means a bare fork starts with none of them: the scene
images would disappear from the UI and every line would be re-synthesised and
re-billed even when the user only asked to respeak one of them.

So the fork copies the parent's assets forward and drops only what the planned
stages will genuinely rewrite. The per-version idempotency guard in the worker then
does the rest of the work for free - a carried-over row makes `find_asset` hit, and
the task returns without calling OpenAI.
"""

from typing import Any

from daastaan_contracts import (
    AssetKind,
    Scope,
    StageName,
    StoryState,
    ValidatedDirective,
)
from sqlalchemy import func
from sqlmodel import Session, select

from .models import MediaAsset, StoryVersion

# These artifacts describe the complete previous episode, rather than one
# reusable source component. A child version must never expose a parent's mix,
# video, or downloaded transcode while its own timeline is still rebuilding.
_TERMINAL_ASSET_KINDS = {
    AssetKind.FINAL_EPISODE.value,
    AssetKind.FINAL_VIDEO.value,
    AssetKind.EPISODE_EXPORT.value,
}


def _scene_rewrite_start(
    state: StoryState,
    *,
    scope: Scope,
    planned: set[StageName],
    target_id: str | None,
) -> int | None:
    """Return the frozen-prefix boundary for a Story Time Machine rewrite.

    A normal scene-image retry only replaces one image. A scene request that
    re-enters at story understanding is different: it replaces the selected
    narrative moment and its future, while every prior scene stays canonical.
    """
    if (
        scope is not Scope.SCENE
        or StageName.STORY_UNDERSTANDING not in planned
        or not target_id
    ):
        return None
    target = state.scene_by_id(target_id)
    return target.index if target is not None else None


def next_version_number(session: Session, *, story_id: str) -> int:
    """Allocate a story-wide revision number under the caller's story lock.

    A historic branch can fork from v1 while v2 is current. Incrementing the
    parent would create a second v2, so revision labels are chronological across
    the whole story rather than along one parent chain.
    """
    latest = session.exec(
        select(func.max(StoryVersion.version_number)).where(StoryVersion.story_id == story_id)
    ).one()
    return int(latest or 0) + 1


def prepare_regeneration_state(
    parent_state_json: dict[str, Any],
    *,
    child_version_id: str,
    scope: Scope,
    target_stage: StageName,
    target_id: str | None,
    instruction_delta: str,
) -> dict[str, Any]:
    """Copy durable narrative state into a child and clear terminal output.

    Media rows are carried independently by :func:`carry_over_assets`. Keeping
    object keys for a parent's final mix in ``state_json`` would make a child
    look ready before assembly has produced its own episode, especially for
    video stories and alternate export formats.
    """
    state = StoryState.model_validate(parent_state_json)
    state.version_id = child_version_id
    state.regen = ValidatedDirective(
        scope=scope.value,
        target_stage=target_stage.value,
        target_id=target_id,
        instruction_delta=instruction_delta,
    )
    state.final_episode_key = None
    state.final_video_key = None
    # These fields are legacy state snapshots; paid media is always discovered
    # from per-version MediaAsset rows. Clearing them prevents stale references
    # from becoming a second, misleading source of truth.
    state.audio_assets = []
    state.image_assets = []
    state.completed_stages = []
    return state.model_dump(mode="json")


def invalidated_dedupe_keys(
    state: StoryState,
    *,
    scope: Scope,
    planned: tuple[StageName, ...] | list[StageName],
    target_id: str | None,
) -> set[str]:
    """Dedupe keys the child version must NOT inherit, because a planned stage is
    about to produce a new artifact for them.

    Anything absent from this set is byte-identical in the child and is reused.
    """
    from . import ids

    planned_set = set(planned)
    stale: set[str] = set()

    rewrite_start = _scene_rewrite_start(
        state, scope=scope, planned=planned_set, target_id=target_id
    )

    if StageName.TTS_SYNTHESIS in planned_set:
        if scope is Scope.LINE and target_id:
            line_ids = [target_id]
        elif scope is Scope.CHARACTER and target_id:
            line_ids = [line.id for line in state.lines if line.character_id == target_id]
        elif rewrite_start is not None:
            scene_indices = {scene.id: scene.index for scene in state.scenes}
            # Prefix dialogue is immutable on a time-machine branch, so its
            # audio can be reused byte-for-byte. Any line at/after the fork (or
            # with an unknown scene) must be re-synthesised.
            line_ids = [
                line.id
                for line in state.lines
                if scene_indices.get(line.scene_id, rewrite_start) >= rewrite_start
            ]
        else:
            line_ids = [line.id for line in state.lines]
        stale |= {ids.dedupe_key(AssetKind.LINE_AUDIO, line_id=lid) for lid in line_ids}

    if StageName.IMAGE_GENERATION in planned_set:
        # A direct image retry only replaces the selected scene. A Story Time
        # Machine rewrite enters at story understanding, however, so every later
        # scene can change and must not inherit stale future artwork.
        if rewrite_start is not None:
            scene_ids = [scene.id for scene in state.scenes if scene.index >= rewrite_start]
        elif scope is Scope.SCENE and target_id:
            scene_ids = [target_id]
        else:
            scene_ids = [scene.id for scene in state.scenes]
        stale |= {ids.dedupe_key(AssetKind.SCENE_IMAGE, scene_id=sid) for sid in scene_ids}

    if StageName.MUSIC_GENERATION in planned_set:
        stale.add(ids.dedupe_key(AssetKind.MUSIC_BED))

    return stale


def carry_over_assets(
    session: Session,
    *,
    parent_version_id: str,
    child_version_id: str,
    state: StoryState,
    scope: Scope,
    planned: tuple[StageName, ...] | list[StageName],
    target_id: str | None,
) -> int:
    """Copy reusable parent assets onto the child version. Returns the copy count.

    The final episode is never carried over. Assembly always re-runs, and letting
    the child keep a row for the old mix would hand the browser the same asset id
    for different audio - a cached stale episode that no reload would clear.

    Copies point at the parent's `object_key`. Stored objects are immutable and are
    never deleted per-version, so sharing bytes across versions is safe and avoids
    duplicating an entire episode's worth of audio on every respeak.
    """
    stale = invalidated_dedupe_keys(state, scope=scope, planned=planned, target_id=target_id)

    parent_assets = session.exec(
        select(MediaAsset).where(MediaAsset.version_id == parent_version_id)
    ).all()

    copied = 0
    for asset in parent_assets:
        if asset.kind in _TERMINAL_ASSET_KINDS or asset.dedupe_key in stale:
            continue
        session.add(
            MediaAsset(
                version_id=child_version_id,
                dedupe_key=asset.dedupe_key,
                kind=asset.kind,
                object_key=asset.object_key,
                content_type=asset.content_type,
                line_id=asset.line_id,
                scene_id=asset.scene_id,
                duration_ms=asset.duration_ms,
                voice_used=asset.voice_used,
                instructions_used=asset.instructions_used,
                size_bytes=asset.size_bytes,
            )
        )
        copied += 1

    session.flush()
    return copied
