"""Celery tasks.

Orchestration shape:

    chain(run_stage x N)  ->  fan_out  ->  chord(group(tts, images), assemble)

The agent stages are sequential and I/O-bound, so one task each buys retry
granularity and a progress stepper rather than parallelism. The real parallelism
is the per-line TTS group. A scoped regeneration is the same chain built from a
shorter slice of the registry, which is why there is no second code path here.
"""

from typing import Any

import structlog
from celery import chain, chord, group
from daastaan_common import carry_over_assets, celery_app, get_store, ids, session_scope
from daastaan_common.models import Feedback, MediaAsset, Story, StoryVersion
from daastaan_contracts import (
    AGENT_STAGES,
    AssetKind,
    JobStatus,
    Queue,
    Scope,
    StageName,
    StoryState,
    StoryStatus,
    TaskName,
    limits,
    plan_stages,
)
from sqlmodel import select

from . import prompts, repo
from .assembly import Clip, compose_episode
from .gateway import PERMANENT_FAILURES, ModelGateway, ModerationBlocked
from .nodes import STAGE_NODES
from .tracing import init_tracing

log = structlog.get_logger(__name__)

RETRY_KWARGS = {
    "autoretry_for": (Exception,),
    "retry_backoff": 5,
    "retry_backoff_max": 120,
    "retry_jitter": True,
    "max_retries": 3,
    # Failures that will repeat identically no matter how many times we try:
    # moderation refusals and unknown targets are decisions rather than outages,
    # and a bad key or malformed request is a configuration bug. Retrying these
    # only delays the error and buries the cause in retry noise. The gateway
    # applies the same distinction to individual HTTP calls.
    "dont_autoretry_for": (
        ModerationBlocked,
        LookupError,
        ValueError,
        *PERMANENT_FAILURES,
    ),
}


def _mark_story(session: Any, story_id: str, status: StoryStatus) -> None:
    story = session.get(Story, story_id)
    if story:
        story.status = status
        session.add(story)
        session.commit()


# --- stage execution -------------------------------------------------------


@celery_app.task(name=TaskName.RUN_STAGE.value, bind=True, **RETRY_KWARGS)
def run_stage(self, version_id: str, stage_value: str, user_id: str) -> str:  # type: ignore[no-untyped-def]
    init_tracing()
    stage = StageName(stage_value)
    node = STAGE_NODES.get(stage)
    if node is None:
        raise ValueError(f"no node registered for stage {stage_value}")

    with session_scope() as session:
        state = repo.load_state(session, version_id)
        job = repo.start_job(
            session, story_id=state.story_id, version_id=version_id, stage=stage
        )
        try:
            gateway = ModelGateway(
                session, stage=stage.value, version_id=version_id, user_id=user_id
            )
            state = node(session, state, gateway)
            if stage not in state.completed_stages:
                state.completed_stages.append(stage)
            repo.save_state(session, state)
            repo.finish_job(session, job, status=JobStatus.SUCCEEDED)
        except Exception as exc:
            repo.finish_job(session, job, status=JobStatus.FAILED, error=str(exc))
            raise
    return version_id


# --- media -----------------------------------------------------------------


@celery_app.task(name=TaskName.TTS_LINE.value, bind=True, **RETRY_KWARGS)
def tts_line(self, version_id: str, line_id: str, user_id: str) -> str | None:  # type: ignore[no-untyped-def]
    init_tracing()
    dedupe = ids.dedupe_key(AssetKind.LINE_AUDIO, line_id=line_id)

    with session_scope() as session:
        # The idempotency guard. With acks_late enabled a task can be redelivered
        # after it already succeeded, and without this check that redelivery would
        # be billed a second time.
        if repo.find_asset(session, version_id=version_id, dedupe_key=dedupe):
            log.info("tts_cached", line_id=line_id)
            return line_id

        state = repo.load_state(session, version_id)
        line = state.line_by_id(line_id)
        if line is None:
            raise LookupError(f"line {line_id} not present in version {version_id}")

        character = state.character_by_id(line.character_id) if line.character_id else None
        voice = (character.voice_preset if character else None) or "alloy"
        instructions = " ".join(
            filter(
                None,
                [
                    character.base_instructions if character else None,
                    line.tts_instructions,
                    f"Emotional intensity {line.intensity} out of 5." if line.intensity else None,
                ],
            )
        )

        gateway = ModelGateway(
            session, stage=StageName.TTS_SYNTHESIS.value, version_id=version_id, user_id=user_id
        )
        audio, duration_ms = gateway.speech(
            text=line.text, voice=voice, instructions=instructions
        )

        key = ids.object_key(version_id, AssetKind.LINE_AUDIO, line_id=line_id, ext="mp3")
        get_store().put(key, audio, "audio/mpeg")
        repo.record_asset(
            session,
            version_id=version_id,
            kind=AssetKind.LINE_AUDIO,
            dedupe_key=dedupe,
            object_key=key,
            content_type="audio/mpeg",
            line_id=line_id,
            duration_ms=duration_ms,
            voice_used=voice,
            instructions_used=instructions[:500],
            size_bytes=len(audio),
        )
        repo.publish(state.story_id, {"type": "asset", "kind": "line_audio", "line_id": line_id})
    return line_id


@celery_app.task(name=TaskName.GEN_IMAGE.value, bind=True, **RETRY_KWARGS)
def gen_image(self, version_id: str, scene_id: str, user_id: str) -> str | None:  # type: ignore[no-untyped-def]
    init_tracing()
    dedupe = ids.dedupe_key(AssetKind.SCENE_IMAGE, scene_id=scene_id)

    with session_scope() as session:
        if repo.find_asset(session, version_id=version_id, dedupe_key=dedupe):
            return scene_id

        state = repo.load_state(session, version_id)
        scene = state.scene_by_id(scene_id)
        if scene is None:
            raise LookupError(f"scene {scene_id} not present in version {version_id}")

        mood = state.mood.mood if state.mood else "neutral"
        prompt = (
            f"{prompts.SCENE_IMAGE}\n\nScene: {scene.title}. {scene.summary}\n"
            f"Setting: {scene.setting}. Mood: {mood}."
        )
        gateway = ModelGateway(
            session, stage=StageName.IMAGE_GENERATION.value, version_id=version_id, user_id=user_id
        )
        image = gateway.image(prompt=prompt)

        key = ids.object_key(version_id, AssetKind.SCENE_IMAGE, scene_id=scene_id, ext="png")
        get_store().put(key, image, "image/png")
        repo.record_asset(
            session,
            version_id=version_id,
            kind=AssetKind.SCENE_IMAGE,
            dedupe_key=dedupe,
            object_key=key,
            content_type="image/png",
            scene_id=scene_id,
            size_bytes=len(image),
        )
        repo.publish(state.story_id, {"type": "asset", "kind": "scene_image", "scene_id": scene_id})
    return scene_id


@celery_app.task(name=TaskName.ASSEMBLE.value, bind=True, **RETRY_KWARGS)
def assemble(self, version_id: str, user_id: str) -> str:  # type: ignore[no-untyped-def]
    """Chord callback. Composes whatever audio exists.

    Deliberately tolerant: if some lines failed every retry, the episode is built
    from the ones that succeeded. A slightly short episode demos; a failed
    pipeline does not.
    """
    init_tracing()
    with session_scope() as session:
        state = repo.load_state(session, version_id)
        # Reaching the callback means the whole media group has drained.
        repo.close_fanout_jobs(session, version_id)
        job = repo.start_job(
            session, story_id=state.story_id, version_id=version_id, stage=StageName.ASSEMBLY
        )
        try:
            assets = {
                asset.line_id: asset
                for asset in session.exec(
                    select(MediaAsset).where(
                        MediaAsset.version_id == version_id,
                        MediaAsset.kind == AssetKind.LINE_AUDIO.value,
                    )
                ).all()
            }
            store = get_store()
            clips = [
                Clip(
                    audio=store.get(assets[line.id].object_key),
                    pause_after_ms=line.pause_after_ms,
                )
                for line in sorted(state.lines, key=lambda line: line.index)
                if line.id in assets
            ]
            missing = len(state.lines) - len(clips)
            if missing:
                log.warning("assembling_with_missing_clips", version_id=version_id, missing=missing)

            episode = compose_episode(clips)
            key = ids.object_key(version_id, AssetKind.FINAL_EPISODE, ext="mp3")
            store.put(key, episode, "audio/mpeg")

            repo.record_asset(
                session,
                version_id=version_id,
                kind=AssetKind.FINAL_EPISODE,
                dedupe_key=ids.dedupe_key(AssetKind.FINAL_EPISODE),
                object_key=key,
                content_type="audio/mpeg",
                size_bytes=len(episode),
            )
            state.final_episode_key = key
            repo.save_state(session, state)
            repo.finish_job(session, job, status=JobStatus.SUCCEEDED)
            _mark_story(session, state.story_id, StoryStatus.READY)
            repo.publish(state.story_id, {"type": "complete", "version_id": version_id})
        except Exception as exc:
            repo.finish_job(session, job, status=JobStatus.FAILED, error=str(exc))
            _mark_story(session, state.story_id, StoryStatus.FAILED)
            raise
    return version_id


# --- orchestration ---------------------------------------------------------


@celery_app.task(name="daastaan.pipeline.fan_out", bind=True)
def fan_out(self, version_id: str, user_id: str, include_images: bool = True) -> str:  # type: ignore[no-untyped-def]
    """Expands the per-line and per-scene work into one chord.

    A chord rather than two groups so assembly runs exactly once, after both
    media kinds finish.

    Assets already present on this version are skipped. On a first run that set is
    empty; on a regeneration it holds everything the fork carried over from the
    parent, so a one-line respeak enqueues one TTS task instead of one per line.
    """
    with session_scope() as session:
        state = repo.load_state(session, version_id)
        existing = {
            asset.dedupe_key
            for asset in session.exec(
                select(MediaAsset).where(MediaAsset.version_id == version_id)
            ).all()
        }
        # Opened here, closed by `assemble`: neither stage has a task of its own
        # to report against, and without these rows the stepper can never pass 80%.
        repo.start_job(
            session,
            story_id=state.story_id,
            version_id=version_id,
            stage=StageName.TTS_SYNTHESIS,
        )
        if include_images:
            repo.start_job(
                session,
                story_id=state.story_id,
                version_id=version_id,
                stage=StageName.IMAGE_GENERATION,
            )

    jobs = [
        tts_line.si(version_id, line.id, user_id)
        for line in state.lines
        if ids.dedupe_key(AssetKind.LINE_AUDIO, line_id=line.id) not in existing
    ]
    if include_images:
        jobs += [
            gen_image.si(version_id, scene.id, user_id)
            for scene in state.scenes[: limits.MAX_IMAGES_PER_STORY]
            if ids.dedupe_key(AssetKind.SCENE_IMAGE, scene_id=scene.id) not in existing
        ]

    if not jobs:
        assemble.si(version_id, user_id).apply_async(queue=Queue.ASSEMBLY.value)
        return version_id

    chord(group(jobs), assemble.si(version_id, user_id)).apply_async()
    return version_id


@celery_app.task(name=TaskName.RUN_PIPELINE.value, bind=True)
def run_pipeline(self, story_id: str, version_id: str, user_id: str) -> str:  # type: ignore[no-untyped-def]
    init_tracing()
    steps = [run_stage.si(version_id, stage.value, user_id) for stage in AGENT_STAGES]
    steps.append(fan_out.si(version_id, user_id))
    chain(*steps).apply_async()
    log.info("pipeline_started", story_id=story_id, version_id=version_id)
    return version_id


@celery_app.task(name=TaskName.REGENERATE.value, bind=True)
def regenerate(  # type: ignore[no-untyped-def]
    self,
    story_id: str,
    version_id: str,
    user_id: str,
    stages: list[str],
    scope: str,
    target_id: str | None,
    instruction_delta: str,
) -> str:
    """Re-enters the pipeline partway through.

    The stage list is re-validated against the registry here rather than trusted
    from the message, so even a tampered queue entry cannot name an arbitrary
    entry point.
    """
    init_tracing()
    requested = [StageName(value) for value in stages]
    allowed = set(plan_stages(Scope(scope), requested[0]))
    planned = [stage for stage in requested if stage in allowed]

    steps = [
        run_stage.si(version_id, stage.value, user_id)
        for stage in planned
        if stage in STAGE_NODES
    ]
    if StageName.TTS_SYNTHESIS in planned or StageName.IMAGE_GENERATION in planned:
        steps.append(
            fan_out.si(version_id, user_id, StageName.IMAGE_GENERATION in planned)
        )
    else:
        steps.append(assemble.si(version_id, user_id))

    chain(*steps).apply_async()
    log.info("regeneration_started", version_id=version_id, stages=[s.value for s in planned])
    return version_id


@celery_app.task(name=TaskName.INTERPRET_FEEDBACK.value, bind=True, **RETRY_KWARGS)
def interpret_feedback(  # type: ignore[no-untyped-def]
    self, story_id: str, version_id: str, user_id: str, feedback_id: str
) -> str:
    """Turns free text into a directive, then hands it to the same validated path
    the explicit regenerate button uses.

    The model's choice is a suggestion. `plan_stages` and the target lookup decide
    what actually runs, so the worst an injected instruction can do is pick a
    different legitimate stage.
    """
    init_tracing()
    from daastaan_contracts.models import RegenDirective

    with session_scope() as session:
        feedback = session.get(Feedback, feedback_id)
        if feedback is None:
            raise LookupError(f"feedback {feedback_id} not found")

        state = repo.load_state(session, version_id)
        gateway = ModelGateway(
            session, stage="feedback_interpreter", version_id=version_id, user_id=user_id
        )
        gateway.moderate(feedback.raw_text)

        summary = {
            "lines": [{"id": line.id, "speaker": line.speaker} for line in state.lines],
            "characters": [{"id": c.id, "name": c.name} for c in state.characters],
            "scenes": [{"id": s.id, "title": s.title} for s in state.scenes],
        }
        directive = gateway.structured(
            schema=RegenDirective,
            system=prompts.FEEDBACK_INTERPRETER,
            user_content=(
                f"Current story structure:\n{summary}\n\n"
                f"<feedback>\n{feedback.raw_text}\n</feedback>"
            ),
            kind="light",
            temperature=0.0,
        )

        try:
            scope = Scope(directive.scope)
            target_stage = StageName(directive.target_stage)
            planned = plan_stages(scope, target_stage)
        except ValueError as exc:
            raise ValueError(f"model proposed an invalid directive: {exc}") from exc

        target_id = _resolve_target(state, scope, directive.target_id)

        parent = session.get(StoryVersion, version_id)
        child = StoryVersion(
            story_id=story_id,
            parent_version_id=version_id,
            version_number=(parent.version_number if parent else 1) + 1,
            genre=parent.genre if parent else None,
            mood=parent.mood if parent else None,
            state_json=dict(parent.state_json) if parent else {},
            created_from_feedback_id=feedback_id,
        )
        session.add(child)
        session.flush()

        if parent:
            carry_over_assets(
                session,
                parent_version_id=parent.id,
                child_version_id=child.id,
                state=state,
                scope=scope,
                planned=planned,
                target_id=target_id,
            )

        child.state_json["version_id"] = child.id
        child.state_json["regen"] = {
            "scope": scope.value,
            "target_stage": target_stage.value,
            "target_id": target_id,
            "instruction_delta": directive.instruction_delta,
        }

        feedback.directive_json = directive.model_dump()
        feedback.resulting_version_id = child.id
        story = session.get(Story, story_id)
        if story:
            story.current_version_id = child.id
            story.status = StoryStatus.GENERATING
        session.commit()
        new_version_id = child.id

    regenerate.si(
        story_id, new_version_id, user_id, [s.value for s in planned], scope.value,
        target_id, directive.instruction_delta,
    ).apply_async(queue=Queue.AGENTS.value)
    return new_version_id


def _resolve_target(state: StoryState, scope: Scope, target_id: str | None) -> str | None:
    """A target the model names must exist in the current state. Unknown ids are
    dropped rather than passed along."""
    if scope is Scope.FULL_STORY or not target_id:
        return None
    lookup = {
        Scope.LINE: state.line_by_id,
        Scope.CHARACTER: state.character_by_id,
        Scope.SCENE: state.scene_by_id,
    }[scope]
    return target_id if lookup(target_id) is not None else None
