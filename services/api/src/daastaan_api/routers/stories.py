from daastaan_common import carry_over_assets
from daastaan_common.models import Job, MediaAsset, Story, StoryVersion
from daastaan_contracts import (
    PIPELINE_STAGES,
    AssetKind,
    Scope,
    StageName,
    StoryState,
    StoryStatus,
    limits,
    plan_stages,
)
from fastapi import APIRouter, HTTPException, status
from sqlmodel import Session, select

from ..deps import CurrentUser, OwnedStory, SessionDep
from ..dispatch import dispatch_pipeline, dispatch_regeneration
from ..guards import audit, enforce_budget, enforce_rate_limit
from ..schemas import (
    AssetOut,
    CreateStoryRequest,
    DispatchAccepted,
    JobOut,
    ProgressOut,
    RegenerateRequest,
    StageProgressOut,
    StoryDetailOut,
    StoryOut,
    VersionOut,
)

router = APIRouter(prefix="/stories", tags=["stories"])


def _asset_out(asset: MediaAsset) -> AssetOut:
    return AssetOut(
        id=asset.id,
        kind=asset.kind,
        line_id=asset.line_id,
        scene_id=asset.scene_id,
        content_type=asset.content_type,
        duration_ms=asset.duration_ms,
        url=f"/api/media/{asset.id}",
    )


@router.post("", response_model=DispatchAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_story(
    body: CreateStoryRequest, session: SessionDep, user: CurrentUser
) -> DispatchAccepted:
    enforce_rate_limit(session, user.id, "generate", limits.RATE_LIMIT_GENERATIONS)
    enforce_budget(session)

    story = Story(user_id=user.id, status=StoryStatus.GENERATING)
    session.add(story)
    session.flush()  # assign story.id before the version references it

    version = StoryVersion(story_id=story.id, version_number=1, genre=body.genre_hint)
    session.add(version)
    session.flush()

    version.state_json = StoryState(
        story_id=story.id,
        version_id=version.id,
        user_id=user.id,
        raw_text=body.raw_text,
        language=body.language,
        genre_hint=body.genre_hint,
        output_format=body.output_format,
    ).model_dump(mode="json")
    story.current_version_id = version.id

    audit(session, actor_user_id=user.id, action="story.create", target_type="story",
          target_id=story.id)
    session.commit()

    task_id = dispatch_pipeline(story_id=story.id, version_id=version.id, user_id=user.id)
    return DispatchAccepted(
        story_id=story.id,
        version_id=version.id,
        stages=[stage.value for stage in StageName],
        task_id=task_id,
    )


@router.get("", response_model=list[StoryOut])
def list_stories(session: SessionDep, user: CurrentUser) -> list[Story]:
    return list(
        session.exec(
            select(Story).where(Story.user_id == user.id).order_by(Story.created_at.desc())
        ).all()
    )


@router.get("/{story_id}", response_model=StoryDetailOut)
def get_story(story: OwnedStory, session: SessionDep) -> StoryDetailOut:
    version = (
        session.get(StoryVersion, story.current_version_id) if story.current_version_id else None
    )
    assets = (
        list(
            session.exec(
                select(MediaAsset).where(MediaAsset.version_id == version.id)
            ).all()
        )
        if version
        else []
    )
    return StoryDetailOut(
        story=StoryOut.model_validate(story, from_attributes=True),
        version=VersionOut.model_validate(version, from_attributes=True) if version else None,
        state=version.state_json if version else None,
        assets=[_asset_out(asset) for asset in assets],
    )


@router.get("/{story_id}/versions", response_model=list[VersionOut])
def list_versions(story: OwnedStory, session: SessionDep) -> list[StoryVersion]:
    return list(
        session.exec(
            select(StoryVersion)
            .where(StoryVersion.story_id == story.id)
            .order_by(StoryVersion.version_number)
        ).all()
    )


@router.get("/{story_id}/jobs", response_model=ProgressOut)
def get_progress(story: OwnedStory, session: SessionDep) -> ProgressOut:
    """Polling endpoint for the progress stepper. The WebSocket is an
    enhancement; this is the path that always works."""
    jobs = session.exec(
        select(Job)
        .where(Job.version_id == story.current_version_id)
        .order_by(Job.created_at)
    ).all()
    version = (
        session.get(StoryVersion, story.current_version_id)
        if story.current_version_id
        else None
    )
    return ProgressOut(
        story_id=story.id,
        version_id=story.current_version_id,
        status=story.status,
        planned_stages=[stage.value for stage in _planned_stages(version)],
        jobs=[JobOut.model_validate(job, from_attributes=True) for job in jobs],
        stage_progress=_fanout_progress(session, version),
    )


def _fanout_progress(session: Session, version: StoryVersion | None) -> list[StageProgressOut]:
    """Per-line and per-scene completion, recomputed from durable state.

    The live stream reports the same figures from a Redis counter. This is the
    version that survives a Redis flush, a worker restart, or a browser that never
    managed to open the stream at all - so the fan-out stages still show real
    movement rather than sitting on a single "running" chip for minutes.

    Placeholder rows are excluded: `claim_asset` commits an empty `object_key`
    before the paid call, so counting those would report a line as finished at the
    moment its generation began.
    """
    if version is None:
        return []
    try:
        state = StoryState.model_validate(version.state_json)
    except Exception:
        # Progress is a read-only convenience; a version whose state cannot be
        # parsed still has stage rows worth returning.
        return []

    assets = list(
        session.exec(
            select(MediaAsset).where(
                MediaAsset.version_id == version.id,
                MediaAsset.object_key != "",
            )
        ).all()
    )

    totals: list[tuple[StageName, AssetKind, int]] = [
        (StageName.TTS_SYNTHESIS, AssetKind.LINE_AUDIO, len(state.lines)),
        (
            StageName.IMAGE_GENERATION,
            AssetKind.SCENE_IMAGE,
            min(len(state.scenes), limits.MAX_IMAGES_PER_STORY),
        ),
    ]
    progress = []
    for stage, kind, total in totals:
        if not total:
            continue
        completed = sum(1 for asset in assets if asset.kind == kind)
        progress.append(
            StageProgressOut(stage=stage.value, completed=min(completed, total), total=total)
        )
    return progress


def _planned_stages(version: StoryVersion | None) -> tuple[StageName, ...]:
    """What this run is actually expected to do.

    A regeneration only touches a slice of the pipeline, so reporting it against
    all ten stages would show a mostly-empty bar and read as though the story had
    been thrown away and restarted.
    """
    regen = (version.state_json or {}).get("regen") if version else None
    if not regen:
        return PIPELINE_STAGES
    try:
        return plan_stages(Scope(regen["scope"]), StageName(regen["target_stage"]))
    except (KeyError, ValueError):
        return PIPELINE_STAGES


@router.post(
    "/{story_id}/regenerate",
    response_model=DispatchAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def regenerate(
    body: RegenerateRequest, story: OwnedStory, session: SessionDep, user: CurrentUser
) -> DispatchAccepted:
    """Explicit, deterministic regeneration.

    The stage plan is computed from the registry rather than taken from the
    request, so a caller cannot name an arbitrary re-entry point.
    """
    enforce_rate_limit(session, user.id, "regenerate", limits.RATE_LIMIT_REGENERATIONS)
    enforce_budget(session)

    parent = session.get(StoryVersion, story.current_version_id)
    if parent is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "story has no generated version yet")

    try:
        stages = plan_stages(Scope(body.scope), StageName(body.target_stage))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    _assert_target_exists(parent, body)

    child = StoryVersion(
        story_id=story.id,
        parent_version_id=parent.id,
        version_number=parent.version_number + 1,
        genre=parent.genre,
        mood=parent.mood,
        state_json=dict(parent.state_json),
    )
    session.add(child)
    session.flush()

    carry_over_assets(
        session,
        parent_version_id=parent.id,
        child_version_id=child.id,
        state=StoryState.model_validate(parent.state_json),
        scope=Scope(body.scope),
        planned=stages,
        target_id=body.target_id,
    )

    child.state_json["version_id"] = child.id
    child.state_json["regen"] = {
        "scope": body.scope.value,
        "target_stage": body.target_stage.value,
        "target_id": body.target_id,
        "instruction_delta": body.instruction_delta,
    }
    story.current_version_id = child.id
    story.status = StoryStatus.GENERATING

    audit(session, actor_user_id=user.id, action="story.regenerate", target_type="story_version",
          target_id=child.id, metadata={"scope": body.scope.value,
                                        "stage": body.target_stage.value})
    session.commit()

    task_id = dispatch_regeneration(
        story_id=story.id,
        version_id=child.id,
        user_id=user.id,
        stages=list(stages),
        scope=body.scope.value,
        target_id=body.target_id,
        instruction_delta=body.instruction_delta,
    )
    return DispatchAccepted(
        story_id=story.id,
        version_id=child.id,
        stages=[stage.value for stage in stages],
        task_id=task_id,
    )


def _assert_target_exists(parent: StoryVersion, body: RegenerateRequest) -> None:
    """A scoped regeneration must point at something that actually exists in the
    parent version, so an arbitrary id cannot be smuggled through to a worker."""
    if body.scope in {Scope.FULL_STORY, Scope.MUSIC}:
        return
    if not body.target_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"scope {body.scope.value} requires a target_id"
        )

    state = StoryState.model_validate(parent.state_json)
    found = {
        Scope.LINE: state.line_by_id,
        Scope.CHARACTER: state.character_by_id,
        Scope.SCENE: state.scene_by_id,
    }[body.scope](body.target_id)

    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown {body.scope.value} target")
