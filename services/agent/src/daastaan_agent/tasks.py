"""Celery tasks.

Orchestration shape:

    run_pipeline (single task, LangGraph in-process)
        -> fan_out -> chord(group(tts, images), assemble)

The agent stages are executed sequentially (with stages 5+6 parallel) inside a
single Celery task via LangGraph. This eliminates seven sequential broker
dispatches while keeping retry granularity for the media fan-out.  A scoped
regeneration still uses the per-stage Celery path because it re-enters the
pipeline partway through.
"""

import time
from typing import Any

import redis as redis_lib
import structlog
from celery import chain, chord, group
from daastaan_common import carry_over_assets, celery_app, get_settings, get_store, ids, session_scope
from daastaan_common.models import Feedback, MediaAsset, Story, StoryVersion
from daastaan_contracts import (
    AssetKind,
    JobStatus,
    Queue,
    Scope,
    StageName,
    StoryState,
    StoryStatus,
    TaskName,
    language_name,
    limits,
    plan_stages,
)
from sqlmodel import select

from . import prompts, repo
from .assembly import AssemblyError, Clip, SceneFrame, build_scene_timeline, compose_episode, compose_video
from .gateway import PERMANENT_FAILURES, ModelGateway, ModerationBlocked
from .graph import run_agent_stages
from .nodes import STAGE_NODES
from .tracing import (
    clear_pipeline_context,
    end_mlflow_run,
    init_tracing,
    set_pipeline_context,
    start_mlflow_run,
)

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


# --- TTS concurrency semaphore -------------------------------------------

_tts_redis: redis_lib.Redis | None = None


def _tts_semaphore() -> redis_lib.Redis:
    global _tts_redis
    if _tts_redis is None:
        _tts_redis = redis_lib.from_url(get_settings().redis_url)
    return _tts_redis


class _TTSSemaphore:
    """Redis-backed semaphore limiting concurrent TTS calls per story."""

    def __init__(self, story_id: str, limit: int = limits.TTS_MAX_CONCURRENCY) -> None:
        self.key = f"tts_sem:{story_id}"
        self.limit = limit

    def __enter__(self) -> "_TTSSemaphore":
        client = _tts_semaphore()
        # Poll until a slot is available. Timeout after 5 minutes.
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            current = int(client.get(self.key) or 0)
            if current < self.limit:
                if client.incr(self.key) <= self.limit:
                    client.expire(self.key, 600)
                    return self
                # Over limit after increment - back off
                client.decr(self.key)
            time.sleep(0.5)
        raise TimeoutError(f"TTS semaphore for {self.key} not acquired within timeout")

    def __exit__(self, *args: Any) -> None:
        try:
            client = _tts_semaphore()
            val = client.decr(self.key)
            if val <= 0:
                client.delete(self.key)
        except Exception:
            log.warning("tts_semaphore_release_failed", key=self.key, exc_info=True)


# --- stage execution (used by regeneration path) -------------------------


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
            # If all retries exhausted, mark the story as failed
            if self.request.retries >= self.max_retries:
                _mark_story(session, state.story_id, StoryStatus.FAILED)
                log.error(
                    "stage_exhausted_retries",
                    stage=stage_value,
                    version_id=version_id,
                    error=str(exc),
                )
            raise
    return version_id


# --- media -----------------------------------------------------------------


@celery_app.task(name=TaskName.TTS_LINE.value, bind=True, **RETRY_KWARGS)
def tts_line(self, version_id: str, line_id: str, user_id: str) -> str | None:  # type: ignore[no-untyped-def]
    init_tracing()
    dedupe = ids.dedupe_key(AssetKind.LINE_AUDIO, line_id=line_id)

    with session_scope() as session:
        # Fast path: already generated (Celery redelivery or prior run).
        if repo.find_asset(session, version_id=version_id, dedupe_key=dedupe):
            log.info("tts_cached", line_id=line_id)
            return line_id

        # Atomic claim: prevents two concurrent workers from both paying.
        claim = repo.claim_asset(
            session, version_id=version_id, kind=AssetKind.LINE_AUDIO, dedupe_key=dedupe
        )
        if claim is None:
            log.info("tts_claimed_by_another", line_id=line_id)
            return line_id

        state = repo.load_state(session, version_id)
        line = state.line_by_id(line_id)
        if line is None:
            repo.release_claim(session, claim)
            raise LookupError(f"line {line_id} not present in version {version_id}")

        character = state.character_by_id(line.character_id) if line.character_id else None
        voice = (character.voice_preset if character else None) or "alloy"
        lang_hint = (
            f"Speak in {language_name(state.language)}."
            if state.language != "en"
            else None
        )
        instructions = " ".join(
            filter(
                None,
                [
                    lang_hint,
                    character.base_instructions if character else None,
                    line.tts_instructions,
                    f"Emotional intensity {line.intensity} out of 5." if line.intensity else None,
                ],
            )
        )

        try:
            gateway = ModelGateway(
                session, stage=StageName.TTS_SYNTHESIS.value, version_id=version_id, user_id=user_id
            )
            with _TTSSemaphore(state.story_id):
                audio, duration_ms = gateway.speech(
                    text=line.text, voice=voice, instructions=instructions
                )
        except Exception:
            repo.release_claim(session, claim)
            raise

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

        claim = repo.claim_asset(
            session, version_id=version_id, kind=AssetKind.SCENE_IMAGE, dedupe_key=dedupe
        )
        if claim is None:
            log.info("image_claimed_by_another", scene_id=scene_id)
            return scene_id

        state = repo.load_state(session, version_id)
        scene = state.scene_by_id(scene_id)
        if scene is None:
            repo.release_claim(session, claim)
            raise LookupError(f"scene {scene_id} not present in version {version_id}")

        mood = state.mood.mood if state.mood else "neutral"
        prompt = (
            f"{prompts.SCENE_IMAGE}\n\nScene: {scene.title}. {scene.summary}\n"
            f"Setting: {scene.setting}. Mood: {mood}."
        )

        try:
            gateway = ModelGateway(
                session, stage=StageName.IMAGE_GENERATION.value,
                version_id=version_id, user_id=user_id,
            )
            image = gateway.image(prompt=prompt)
        except Exception:
            repo.release_claim(session, claim)
            raise

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
                if line.id in assets and assets[line.id].object_key
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

            if state.output_format in ("video", "both"):
                compose_video_task.si(version_id, user_id).apply_async(
                    queue=Queue.ASSEMBLY.value
                )
            else:
                _mark_story(session, state.story_id, StoryStatus.READY)
                repo.publish(state.story_id, {"type": "complete", "version_id": version_id})
        except Exception as exc:
            repo.finish_job(session, job, status=JobStatus.FAILED, error=str(exc))
            _mark_story(session, state.story_id, StoryStatus.FAILED)
            raise
    return version_id


@celery_app.task(name=TaskName.COMPOSE_VIDEO.value, bind=True, **RETRY_KWARGS)
def compose_video_task(self, version_id: str, user_id: str) -> str:  # type: ignore[no-untyped-def]
    """Compose scene images and the final audio into an MP4 video."""
    init_tracing()
    with session_scope() as session:
        state = repo.load_state(session, version_id)
        job = repo.start_job(
            session, story_id=state.story_id, version_id=version_id,
            stage=StageName.VIDEO_COMPOSITION,
        )
        try:
            if state.output_format not in ("video", "both"):
                repo.finish_job(session, job, status=JobStatus.SKIPPED)
                _mark_story(session, state.story_id, StoryStatus.READY)
                repo.publish(state.story_id, {"type": "complete", "version_id": version_id})
                return version_id

            # Load audio assets for timeline calculation
            audio_assets = {
                asset.line_id: asset
                for asset in session.exec(
                    select(MediaAsset).where(
                        MediaAsset.version_id == version_id,
                        MediaAsset.kind == AssetKind.LINE_AUDIO.value,
                    )
                ).all()
            }

            # Load scene image assets
            image_assets = {
                asset.scene_id: asset
                for asset in session.exec(
                    select(MediaAsset).where(
                        MediaAsset.version_id == version_id,
                        MediaAsset.kind == AssetKind.SCENE_IMAGE.value,
                    )
                ).all()
            }

            # Load the final episode audio
            if not state.final_episode_key:
                raise AssemblyError("no final episode audio available for video composition")
            store = get_store()
            episode_audio = store.get(state.final_episode_key)

            # Build scene timeline
            timeline = build_scene_timeline(state.lines, audio_assets)

            # Build SceneFrame list, skipping scenes without images
            scene_frames: list[SceneFrame] = []
            for scene in sorted(state.scenes, key=lambda s: s.index):
                if scene.id not in image_assets or not image_assets[scene.id].object_key:
                    log.warning("video_missing_scene_image", scene_id=scene.id)
                    continue
                if scene.id not in timeline:
                    log.warning("video_missing_scene_timeline", scene_id=scene.id)
                    continue
                start_ms, end_ms = timeline[scene.id]
                duration_ms = end_ms - start_ms
                if duration_ms <= 0:
                    continue
                image_bytes = store.get(image_assets[scene.id].object_key)
                scene_frames.append(SceneFrame(
                    image=image_bytes,
                    duration_ms=duration_ms,
                    scene_id=scene.id,
                ))

            if not scene_frames:
                raise AssemblyError("no scene frames available for video composition")

            video = compose_video(episode_audio, scene_frames)

            key = ids.object_key(version_id, AssetKind.FINAL_VIDEO, ext="mp4")
            store.put(key, video, "video/mp4")

            repo.record_asset(
                session,
                version_id=version_id,
                kind=AssetKind.FINAL_VIDEO,
                dedupe_key=ids.dedupe_key(AssetKind.FINAL_VIDEO),
                object_key=key,
                content_type="video/mp4",
                size_bytes=len(video),
            )
            state.final_video_key = key
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

    if state.output_format in ("video", "both"):
        include_images = True

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


def _on_pipeline_error(version_id: str, user_id: str) -> None:
    """link_error callback: mark the story FAILED when any chained task fails."""
    with session_scope() as session:
        state = repo.load_state(session, version_id)
        _mark_story(session, state.story_id, StoryStatus.FAILED)


@celery_app.task(name="daastaan.pipeline.on_error", bind=True)
def pipeline_error_handler(self, request, exc, traceback, version_id: str = "", **kwargs) -> None:  # type: ignore[no-untyped-def]
    """Celery link_error handler for regeneration chains."""
    if not version_id:
        return
    with session_scope() as session:
        try:
            state = repo.load_state(session, version_id)
            _mark_story(session, state.story_id, StoryStatus.FAILED)
            log.error("chain_failed_marking_story", version_id=version_id, error=str(exc))
        except Exception:
            log.error("error_handler_failed", version_id=version_id, exc_info=True)


@celery_app.task(name=TaskName.RUN_PIPELINE.value, bind=True)
def run_pipeline(self, story_id: str, version_id: str, user_id: str) -> str:  # type: ignore[no-untyped-def]
    """Execute all reasoning stages via LangGraph, then dispatch media fan-out.

    The graph runs every node in-process, eliminating seven sequential broker
    dispatches. If any node fails, the story is immediately marked FAILED.
    """
    init_tracing()
    pipeline_run = None
    mlflow_run_id = None

    try:
        # Start tracking
        mlflow_run_id = start_mlflow_run(version_id=version_id, story_id=story_id)

        with session_scope() as session:
            pipeline_run = repo.start_pipeline_run(
                session, version_id=version_id, mlflow_run_id=mlflow_run_id
            )
            ctx = set_pipeline_context(
                pipeline_run_id=pipeline_run.id,
                story_id=story_id,
                version_id=version_id,
            )

            state = repo.load_state(session, version_id)

            t0 = time.monotonic()
            state = run_agent_stages(session, state, user_id)
            total_s = time.monotonic() - t0

            repo.save_state(session, state)
            repo.finish_pipeline_run(session, pipeline_run, status="succeeded")

            log.info(
                "pipeline_stages_completed",
                story_id=story_id,
                version_id=version_id,
                total_s=round(total_s, 2),
                stages=len(state.completed_stages),
                lines=len(state.lines),
                scenes=len(state.scenes),
                characters=len(state.characters),
            )

        # End MLflow with metrics
        metrics: dict[str, float] = {
            "total_duration_s": round(total_s, 3),
            "line_count": len(state.lines),
            "scene_count": len(state.scenes),
            "character_count": len(state.characters),
        }
        if ctx.stage_timings:
            metrics.update({f"stage_{k}_s": round(v, 3) for k, v in ctx.stage_timings.items()})
        end_mlflow_run(status="FINISHED", metrics=metrics)

    except Exception as exc:
        log.error(
            "pipeline_failed",
            story_id=story_id,
            version_id=version_id,
            error=str(exc),
        )
        with session_scope() as session:
            _mark_story(session, story_id, StoryStatus.FAILED)
            if pipeline_run:
                try:
                    run = session.get(type(pipeline_run), pipeline_run.id)
                    if run:
                        repo.finish_pipeline_run(session, run, status="failed", error=str(exc))
                except Exception:
                    log.warning("pipeline_run_finish_failed", exc_info=True)
        end_mlflow_run(status="FAILED")
        raise
    finally:
        clear_pipeline_context()

    # Dispatch media generation
    fan_out.si(version_id, user_id).apply_async(queue=Queue.MEDIA.value)
    log.info("pipeline_started_media", story_id=story_id, version_id=version_id)
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

    c = chain(*steps)
    error_cb = pipeline_error_handler.si(version_id=version_id)
    c.apply_async(link_error=error_cb)
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
