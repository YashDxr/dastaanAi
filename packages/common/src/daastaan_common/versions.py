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

from daastaan_contracts import AssetKind, Scope, StageName, StoryState
from daastaan_contracts.limits import SHOT_TAGS
from sqlmodel import Session, select

from .models import MediaAsset


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

    if StageName.TTS_SYNTHESIS in planned_set:
        if scope is Scope.LINE and target_id:
            line_ids = [target_id]
        elif scope is Scope.CHARACTER and target_id:
            line_ids = [line.id for line in state.lines if line.character_id == target_id]
        else:
            line_ids = [line.id for line in state.lines]
        stale |= {ids.dedupe_key(AssetKind.LINE_AUDIO, line_id=lid) for lid in line_ids}

    if StageName.IMAGE_GENERATION in planned_set:
        if scope is Scope.SCENE and target_id:
            scene_ids = [target_id]
        else:
            scene_ids = [scene.id for scene in state.scenes]
        scene_id_set = set(scene_ids)
        for sid in scene_ids:
            stale.add(ids.dedupe_key(AssetKind.SCENE_IMAGE, scene_id=sid))
            for tag in SHOT_TAGS:
                stale.add(ids.dedupe_key(AssetKind.SCENE_IMAGE, scene_id=sid, tag=tag))
        # Per-line image keys (used by video mode)
        for line in state.lines:
            if line.scene_id in scene_id_set:
                stale.add(ids.dedupe_key(AssetKind.SCENE_IMAGE, line_id=line.id))

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

    Edited cuts are not carried over either, for a stronger reason: a cut is a
    render of one particular version's footage, and its `video_edits` row still
    points at that version. Copying the MP4 forward would put a second row for the
    same render on a version whose script may no longer match it.

    Uploaded backing tracks *are* carried over. Nothing in the pipeline produced
    them, so no regeneration can invalidate them, and a listener who uploaded a
    song should not have to upload it again because they respoke one line.

    Copies point at the parent's `object_key`. Stored objects are immutable and are
    never deleted per-version, so sharing bytes across versions is safe and avoids
    duplicating an entire episode's worth of audio on every respeak.
    """
    stale = invalidated_dedupe_keys(state, scope=scope, planned=planned, target_id=target_id)

    parent_assets = session.exec(
        select(MediaAsset).where(MediaAsset.version_id == parent_version_id)
    ).all()

    not_inherited = {
        AssetKind.FINAL_EPISODE.value,
        AssetKind.FINAL_VIDEO.value,
        AssetKind.EDITED_VIDEO.value,
    }

    copied = 0
    for asset in parent_assets:
        if asset.kind in not_inherited or asset.dedupe_key in stale:
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
