"""Celery tasks.

Orchestration shape:

    run_pipeline (single task, LangGraph in-process)
        -> fan_out -> chord(group(tts, images, optional music), assemble)

The agent stages are executed sequentially (with stages 5+6 parallel) inside a
single Celery task via LangGraph. This eliminates seven sequential broker
dispatches while keeping retry granularity for the media fan-out.  A scoped
regeneration still uses the per-stage Celery path because it re-enters the
pipeline partway through.
"""

import json
import time
from uuid import uuid4
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import redis as redis_lib
import structlog
from celery import chain, chord, group
from daastaan_common import (
    carry_over_assets,
    celery_app,
    get_settings,
    get_store,
    ids,
    session_scope,
)
from daastaan_common.models import Feedback, IngestJob, MediaAsset, Story, StoryVersion
from daastaan_contracts import (
    AssetKind,
    FeedbackStatus,
    IngestStatus,
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
    MysteryCaseOutput,
    MysteryInterrogationOutput,
    MysteryValidationOutput,
)
from sqlmodel import select

from . import prompts, repo
from .assembly import (
    AUDIO_EXPORTS,
    BGM_AUDIO_EXPORTS,
    AssemblyError,
    Clip,
    SceneFrame,
    bgm_content_type,
    compose_episode,
    compose_video,
    export_content_type,
    transcode,
    transcode_bgm,
)

# Safe to import eagerly: the module keeps pypdf, tesseract and PIL behind
# function-local imports so the API and the non-ingest workers never load them.
from .ingest import ExtractionError

if TYPE_CHECKING:
    from .ingest import Extraction as IngestExtraction
from .gateway import PERMANENT_FAILURES, ModelGateway, ModerationBlocked
from .graph import run_agent_stages
from .music import MusicServiceClient, MusicServiceError, build_music_brief
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

MYSTERY_SYSTEM = """You are Daastaan's fair-play mystery showrunner. Create an original, cinematic and logically solvable case using broad genre conventions (noir moral ambiguity, cozy social observation, thriller pressure, supernatural dread, or classical clue craft), never imitate a named author or existing work. Return only the requested JSON; no chain-of-thought. There must be exactly one culprit whose name exactly matches one suspect. Every suspect needs a public alibi, a believable private secret, motive, and relationship. Provide at least five independent clues, with three that fairly identify the culprit. Red herrings must be compatible with the solution. Difficulty controls ambiguity and clue clarity, not whether the case is solvable. Do not reveal the culprit in title, premise, initial scene, alibis, or clue titles."""
INTERROGATION_SYSTEM = """You are roleplaying a murder-mystery suspect. Return only structured JSON. Never state the killer identity, solution, or a private secret. Answer in character, concise, emotionally grounded, and plausibly evasive where appropriate. You may only use the supplied public suspect facts and already discovered clues."""
MYSTERY_CRITIC_SYSTEM = """You are a strict fair-play mystery editor. Inspect the supplied private mystery JSON. Return only the requested JSON, with concise issue labels and no hidden reasoning. A valid case has exactly one culprit, an internally coherent timeline, at least three independent fair clues identifying that culprit, no contradictory red herrings, and no culprit/solution leakage in public-facing title, premise, initial scene, suspect alibis, or clue titles."""


# Ingest gets its own policy. A file that cannot be parsed will not parse on the
# fourth attempt either, so `ExtractionError` is terminal; only transport and
# model outages are worth another go, and OCR is expensive enough that one retry
# is the right ceiling.
INGEST_RETRY_KWARGS: dict[str, Any] = {
    **RETRY_KWARGS,
    "max_retries": 1,
    "dont_autoretry_for": (*RETRY_KWARGS["dont_autoretry_for"], ExtractionError),
}

# How much of a document is screened by moderation, and how much reaches the
# cleanup model. Both are prefixes: the point is to catch what a story is, not
# to pay to reason over a whole book.
MODERATION_SAMPLE_CHARS = 20_000
MAX_CLEANUP_INPUT_CHARS = 120_000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _bypass_cache(state: StoryState) -> bool:
    """Whether this run must ignore the response cache.

    Every stage a regeneration executes was picked because the user wants a
    different result. Some of those stages send inputs identical to the previous
    run - a line respeak reaches TTS with the same text, voice and instructions -
    so serving a cached response would return the take being replaced and the
    regeneration would look like it did nothing.
    """
    return state.regen is not None


def _mark_story(session: Any, story_id: str, status: StoryStatus) -> None:
    story = session.get(Story, story_id)
    if story:
        story.status = status
        session.add(story)
        session.commit()


@celery_app.task(name=TaskName.GENERATE_MYSTERY.value, bind=True, **RETRY_KWARGS)
def generate_mystery(self, story_id: str, version_id: str, user_id: str) -> str:
    """Generate and persist the complete private case in the existing version state."""
    with session_scope() as session:
        state = repo.load_state(session, version_id)
        job = repo.start_job(session, story_id=story_id, version_id=version_id, stage="mystery_generation")
        try:
            request = (state.mystery or {}).get("request", {})
            gateway = ModelGateway(session, stage="mystery_generation", version_id=version_id, user_id=user_id)
            result = gateway.structured(schema=MysteryCaseOutput, system=MYSTERY_SYSTEM, user_content=json.dumps(request), kind="reasoning")
            expected_suspects = int(request.get("suspect_count", 4))
            requested_names = {name.casefold() for name in request.get("suspect_names", [])}
            generated_names = {suspect.name.casefold() for suspect in result.suspects}
            if len(result.suspects) != expected_suspects or result.culprit_name not in {s.name for s in result.suspects} or len(result.clues) < 5 or not requested_names.issubset(generated_names):
                raise ValueError("generated mystery did not pass solvability validation")
            critic = gateway.structured(schema=MysteryValidationOutput, system=MYSTERY_CRITIC_SYSTEM, user_content=result.model_dump_json(), kind="reasoning", temperature=0.1)
            if not critic.valid or critic.public_spoiler_detected or critic.fair_clue_count < 3:
                repair = gateway.structured(schema=MysteryCaseOutput, system=MYSTERY_SYSTEM, user_content=json.dumps({"request": request, "draft": result.model_dump(mode="json"), "repair_issues": critic.issues, "instruction": "Repair only the flagged sections while preserving valid characters, setting, and clues."}), kind="reasoning")
                result = repair
                critic = gateway.structured(schema=MysteryValidationOutput, system=MYSTERY_CRITIC_SYSTEM, user_content=result.model_dump_json(), kind="reasoning", temperature=0.1)
                if not critic.valid or critic.public_spoiler_detected or critic.fair_clue_count < 3:
                    raise ValueError("mystery consistency validation failed")
            suspects = [{"id": str(uuid4()), **s.model_dump(), "is_culprit": s.name == result.culprit_name} for s in result.suspects]
            culprit = next(s for s in suspects if s["is_culprit"])
            clues = [{"id": str(uuid4()), **c.model_dump()} for c in result.clues]
            state.title = result.title
            state.setting = result.setting
            state.mystery = {
                "id": str(uuid4()), "title": result.title, "premise": result.premise, "setting": result.setting,
                "victim": request.get("victim_name") or result.victim, "difficulty": request.get("difficulty", "medium"), "tone": request.get("tone", "classic_whodunit"), "duration_minutes": request.get("duration_minutes", 20), "suspects": suspects,
                "culprit_id": culprit["id"], "culprit_motive": result.culprit_motive,
                "crime_timeline": [x.model_dump() for x in result.crime_timeline], "clues": clues,
                "red_herrings": result.red_herrings, "solution": result.solution, "reveal_scene": result.reveal_scene,
                "initial_scene": result.initial_scene,
                "consistency_checks": {"valid": critic.valid, "fair_clue_count": critic.fair_clue_count},
            }
            state.mystery_play = {"discovered_clue_ids": [], "interrogations": [], "accusations": [], "revealed": False}
            repo.save_state(session, state); repo.finish_job(session, job, status=JobStatus.SUCCEEDED); _mark_story(session, story_id, StoryStatus.READY); repo.publish(story_id, {"type": "complete", "version_id": version_id})
        except Exception as exc:
            repo.finish_job(session, job, status=JobStatus.FAILED, error=str(exc)); _mark_story(session, story_id, StoryStatus.FAILED); raise
    return version_id


@celery_app.task(name=TaskName.INTERROGATE_MYSTERY.value, bind=True, **RETRY_KWARGS)
def interrogate_mystery(self, version_id: str, user_id: str, suspect_id: str, question: str) -> str:
    with session_scope() as session:
        state = repo.load_state(session, version_id); case = state.mystery or {}; play = state.mystery_play or {}
        suspect = next((s for s in case.get("suspects", []) if s["id"] == suspect_id), None)
        if not suspect: raise LookupError("suspect not found")
        discovered = [c for c in case.get("clues", []) if c["id"] in set(play.get("discovered_clue_ids", []))]
        # Private secrets and culpability never enter the interrogation prompt.
        # This makes redaction structural rather than relying on the model to obey.
        allowed = {k: v for k, v in suspect.items() if k not in {"is_culprit", "motive", "secret"}}
        result = ModelGateway(session, stage="mystery_interrogation", version_id=version_id, user_id=user_id).structured(schema=MysteryInterrogationOutput, system=INTERROGATION_SYSTEM, user_content=str({"suspect": allowed, "discovered_clues": discovered, "question": question}), kind="light")
        play.setdefault("interrogations", []).append({"id": str(uuid4()), "suspect_id": suspect_id, "question": question, **result.model_dump()})
        state.mystery_play = play; repo.save_state(session, state); repo.publish(state.story_id, {"type": "mystery_interrogation", "suspect_id": suspect_id})
    return version_id


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
                session,
                stage=stage.value,
                version_id=version_id,
                user_id=user_id,
                story_id=state.story_id,
                bypass_cache=_bypass_cache(state),
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
                session,
                stage=StageName.TTS_SYNTHESIS.value,
                version_id=version_id,
                user_id=user_id,
                story_id=state.story_id,
                bypass_cache=_bypass_cache(state),
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
def gen_image(self, version_id: str, scene_id: str, user_id: str, shot_type: str | None = None, line_id: str | None = None) -> str | None:  # type: ignore[no-untyped-def]
    init_tracing()
    if line_id:
        dedupe = ids.dedupe_key(AssetKind.SCENE_IMAGE, line_id=line_id)
    else:
        dedupe = ids.dedupe_key(AssetKind.SCENE_IMAGE, scene_id=scene_id, tag=shot_type)

    with session_scope() as session:
        if repo.find_asset(session, version_id=version_id, dedupe_key=dedupe):
            return scene_id

        claim = repo.claim_asset(
            session, version_id=version_id, kind=AssetKind.SCENE_IMAGE, dedupe_key=dedupe
        )
        if claim is None:
            log.info("image_claimed_by_another", scene_id=scene_id, shot_type=shot_type, line_id=line_id)
            return scene_id

        state = repo.load_state(session, version_id)
        scene = state.scene_by_id(scene_id)
        if scene is None:
            repo.release_claim(session, claim)
            raise LookupError(f"scene {scene_id} not present in version {version_id}")

        mood = state.mood.mood if state.mood else "neutral"

        if line_id:
            line = state.line_by_id(line_id)
            if line is None:
                repo.release_claim(session, claim)
                raise LookupError(f"line {line_id} not present in version {version_id}")
            prompt = prompts.line_image_prompt(shot_type or "mid", scene, line, mood)
        else:
            image_prompt = prompts.SHOT_PROMPTS[shot_type] if shot_type else prompts.SCENE_IMAGE
            prompt = (
                f"{image_prompt}\n\nScene: {scene.title}. {scene.summary}\n"
                f"Setting: {scene.setting}. Mood: {mood}."
            )

        try:
            gateway = ModelGateway(
                session,
                stage=StageName.IMAGE_GENERATION.value,
                version_id=version_id,
                user_id=user_id,
                story_id=state.story_id,
                bypass_cache=_bypass_cache(state),
            )
            image = gateway.image(prompt=prompt)
        except Exception:
            repo.release_claim(session, claim)
            raise

        if line_id:
            key = ids.object_key(version_id, AssetKind.SCENE_IMAGE, line_id=line_id, ext="png")
        else:
            key = ids.object_key(version_id, AssetKind.SCENE_IMAGE, scene_id=scene_id, tag=shot_type, ext="png")
        get_store().put(key, image, "image/png")
        repo.record_asset(
            session,
            version_id=version_id,
            kind=AssetKind.SCENE_IMAGE,
            dedupe_key=dedupe,
            object_key=key,
            content_type="image/png",
            scene_id=scene_id,
            line_id=line_id,
            size_bytes=len(image),
        )
        repo.publish(state.story_id, {"type": "asset", "kind": "scene_image", "scene_id": scene_id})
    return scene_id


@celery_app.task(name=TaskName.GEN_MUSIC.value, bind=True)
def gen_music(self, version_id: str, user_id: str) -> str | None:  # type: ignore[no-untyped-def]
    """Generate one loopable instrumental bed through the private Mac sidecar.

    This task intentionally does not use the generic autoretry decorator.  A
    music host being asleep must degrade one story to narration-only output, not
    fail the Celery chord or stall the rest of the media pipeline. The sidecar
    persists an idempotent job, so an empty asset placeholder left by a worker
    crash is deliberately reused on redelivery rather than treated as complete.
    """
    del user_id  # The local service has no user concept; it receives only a safe brief.
    init_tracing()
    dedupe = ids.dedupe_key(AssetKind.MUSIC_BED)
    settings = get_settings()

    with session_scope() as session:
        state = repo.load_state(session, version_id)
        job = repo.start_job(
            session,
            story_id=state.story_id,
            version_id=version_id,
            stage=StageName.MUSIC_GENERATION,
        )
        if not settings.music_enabled:
            repo.finish_job(session, job, status=JobStatus.SKIPPED)
            return None

        existing = repo.find_asset(session, version_id=version_id, dedupe_key=dedupe)
        if existing and existing.object_key:
            repo.finish_job(session, job, status=JobStatus.SKIPPED)
            return None

        # `claim_asset()` commits an empty object_key before paid work starts.
        # Keep that durable slot through worker loss: submitting the same
        # idempotency key retrieves the sidecar job rather than launching a
        # second MLX process. A concurrent claimant safely converges on it too.
        claim = existing
        if claim is None:
            claim = repo.claim_asset(
                session,
                version_id=version_id,
                kind=AssetKind.MUSIC_BED,
                dedupe_key=dedupe,
            )
        if claim is None:
            claim = repo.find_asset(session, version_id=version_id, dedupe_key=dedupe)
        if claim is None:
            repo.finish_job(session, job, status=JobStatus.SKIPPED)
            return None

        try:
            brief = build_music_brief(state, settings)
            idempotency_key = f"{version_id}:{dedupe}"
            with MusicServiceClient(settings) as client:
                generated = client.generate(idempotency_key=idempotency_key, brief=brief)
                key = ids.object_key(version_id, AssetKind.MUSIC_BED, ext="wav")
                get_store().put(key, generated.audio, "audio/wav")
                # `record_asset` commits before the sidecar output is removed.
                # If this worker dies before then, redelivery can download the
                # same durable job and complete this storage transaction.
                repo.record_asset(
                    session,
                    version_id=version_id,
                    kind=AssetKind.MUSIC_BED,
                    dedupe_key=dedupe,
                    object_key=key,
                    content_type="audio/wav",
                    duration_ms=generated.duration_ms,
                    instructions_used=brief.prompt[:500],
                    size_bytes=len(generated.audio),
                )
                try:
                    client.delete_job(generated.job_id)
                except MusicServiceError:
                    # A TTL sweeper on the Mac will reclaim this output. The
                    # shared copy is already committed, so cleanup is optional.
                    log.warning("music_sidecar_cleanup_failed", job_id=generated.job_id)
            repo.finish_job(session, job, status=JobStatus.SUCCEEDED)
            repo.publish(
                state.story_id,
                {
                    "type": "asset",
                    "kind": AssetKind.MUSIC_BED.value,
                    "duration_ms": generated.duration_ms,
                },
            )
            log.info(
                "music_generated",
                version_id=version_id,
                job_id=generated.job_id,
                duration_ms=generated.duration_ms,
                seed=generated.seed,
            )
            return generated.job_id
        except MusicServiceError as exc:
            # The score is optional. Preserve the empty slot and the sidecar's
            # idempotent job for a redelivery/retry, but make the user-visible
            # stage neutral rather than falsely failing a playable episode.
            public_error = "Background score unavailable; narration will continue."
            repo.finish_job(session, job, status=JobStatus.SKIPPED, error=public_error)
            repo.publish(
                state.story_id,
                {
                    "type": "music_status",
                    "status": "unavailable",
                    "error": public_error,
                },
            )
            log.warning("music_generation_degraded", version_id=version_id, error=str(exc))
            return None
        except Exception as exc:
            # Storage, database, and programming failures are not optional.
            # Let Celery report the real failure instead of silently delivering
            # an episode whose durable media state is inconsistent.
            repo.finish_job(session, job, status=JobStatus.FAILED, error=str(exc))
            log.exception("music_generation_failed", version_id=version_id)
            raise


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

            music_asset = session.exec(
                select(MediaAsset).where(
                    MediaAsset.version_id == version_id,
                    MediaAsset.kind == AssetKind.MUSIC_BED.value,
                    MediaAsset.object_key != "",
                )
            ).first()
            music_bed = None
            if music_asset is not None:
                try:
                    music_bed = store.get(music_asset.object_key)
                except Exception:
                    # A missing optional cue must never destroy a successfully
                    # synthesised narration. The media worker will report the
                    # BGM failure separately and the episode remains playable.
                    log.warning(
                        "music_asset_missing_during_assembly",
                        version_id=version_id,
                        asset_id=music_asset.id,
                        exc_info=True,
                    )

            episode = compose_episode(clips, music_bed=music_bed)
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

            # Load scene image assets keyed by line_id (per-line images)
            # and also by scene_id (fallback for audio-only thumbnails)
            image_by_line: dict[str, MediaAsset] = {}
            image_by_scene: dict[str, MediaAsset] = {}
            for asset in session.exec(
                select(MediaAsset).where(
                    MediaAsset.version_id == version_id,
                    MediaAsset.kind == AssetKind.SCENE_IMAGE.value,
                )
            ).all():
                if asset.object_key:
                    if asset.line_id:
                        image_by_line[asset.line_id] = asset
                    elif asset.scene_id:
                        image_by_scene[asset.scene_id] = asset

            # Load the final episode audio
            if not state.final_episode_key:
                raise AssemblyError("no final episode audio available for video composition")
            store = get_store()
            episode_audio = store.get(state.final_episode_key)

            # Build per-line frames: each line gets its own image with duration = audio + pause
            scene_frames: list[SceneFrame] = []
            for line in sorted(state.lines, key=lambda l: l.index):
                img_asset = image_by_line.get(line.id)
                if img_asset is None:
                    # Fallback: try scene-level image
                    img_asset = image_by_scene.get(line.scene_id)
                if img_asset is None:
                    log.warning("video_missing_line_image", line_id=line.id, scene_id=line.scene_id)
                    continue

                audio_asset = audio_assets.get(line.id)
                audio_dur = audio_asset.duration_ms if audio_asset and audio_asset.duration_ms else 0
                duration_ms = audio_dur + (line.pause_after_ms or 0)
                if duration_ms <= 0:
                    continue

                image_bytes = store.get(img_asset.object_key)
                scene_frames.append(SceneFrame(
                    image=image_bytes,
                    duration_ms=duration_ms,
                    scene_id=line.scene_id,
                    text=line.text,
                    speaker=line.speaker or "",
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
def fan_out(
    self,
    version_id: str,
    user_id: str,
    include_images: bool = True,
    include_music: bool | None = None,
) -> str:  # type: ignore[no-untyped-def]
    """Expands the per-line and per-scene work into one chord.

    A chord rather than two groups so assembly runs exactly once, after both
    media kinds finish.

    Assets already present on this version are skipped. On a first run that set is
    empty; on a regeneration it holds everything the fork carried over from the
    parent, so a one-line respeak enqueues one TTS task instead of one per line.
    """
    settings = get_settings()
    music_stage_requested = include_music is None or include_music
    if include_music is None:
        include_music = settings.music_enabled

    with session_scope() as session:
        state = repo.load_state(session, version_id)
        existing_assets = list(
            session.exec(
                select(MediaAsset).where(MediaAsset.version_id == version_id)
            ).all()
        )
        existing = {asset.dedupe_key for asset in existing_assets}
        music_complete = any(
            asset.dedupe_key == ids.dedupe_key(AssetKind.MUSIC_BED) and asset.object_key
            for asset in existing_assets
        )
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
        if music_stage_requested and not settings.music_enabled:
            music_job = repo.start_job(
                session,
                story_id=state.story_id,
                version_id=version_id,
                stage=StageName.MUSIC_GENERATION,
            )
            repo.finish_job(
                session,
                music_job,
                status=JobStatus.SKIPPED,
                error="Background score is disabled.",
            )

    if state.output_format in ("video", "both"):
        include_images = True

    jobs = [
        tts_line.si(version_id, line.id, user_id)
        for line in state.lines
        if ids.dedupe_key(AssetKind.LINE_AUDIO, line_id=line.id) not in existing
    ]
    if include_images:
        if state.output_format in ("video", "both"):
            # One image per dialogue line, cycling shot framings for variety
            image_count = 0
            for scene in state.scenes:
                scene_lines = [l for l in state.lines if l.scene_id == scene.id]
                for i, line in enumerate(sorted(scene_lines, key=lambda l: l.index)):
                    if image_count >= limits.MAX_IMAGES_PER_STORY:
                        break
                    shot = limits.SHOT_TAGS[i % len(limits.SHOT_TAGS)]
                    dk = ids.dedupe_key(AssetKind.SCENE_IMAGE, line_id=line.id)
                    if dk not in existing:
                        jobs.append(gen_image.si(version_id, scene.id, user_id, shot, line.id))
                    image_count += 1
                if image_count >= limits.MAX_IMAGES_PER_STORY:
                    break
        else:
            jobs += [
                gen_image.si(version_id, scene.id, user_id)
                for scene in state.scenes[: limits.MAX_IMAGES_PER_STORY]
                if ids.dedupe_key(AssetKind.SCENE_IMAGE, scene_id=scene.id) not in existing
            ]
    # A committed empty slot is a recoverable interrupted generation, not an
    # asset. Re-submit it under the same sidecar idempotency key so it resumes
    # safely instead of producing a permanent narration-only version.
    music_missing = not music_complete
    if include_music and settings.music_enabled and music_missing:
        jobs.append(gen_music.si(version_id, user_id))

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
            pipeline_run_id = pipeline_run.id

        # Each graph node opens its own session_scope(), so parallel
        # branches (stages 5+6) never share a SQLAlchemy session.
        t0 = time.monotonic()
        state = run_agent_stages(state, user_id)
        total_s = time.monotonic() - t0

        with session_scope() as session:
            repo.save_state(session, state)
            run = session.get(type(pipeline_run), pipeline_run_id)
            if run:
                repo.finish_pipeline_run(session, run, status="succeeded")

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
    if (
        StageName.TTS_SYNTHESIS in planned
        or StageName.IMAGE_GENERATION in planned
        or StageName.MUSIC_GENERATION in planned
    ):
        steps.append(
            fan_out.si(
                version_id,
                user_id,
                StageName.IMAGE_GENERATION in planned,
                StageName.MUSIC_GENERATION in planned,
            )
        )
    else:
        steps.append(assemble.si(version_id, user_id))

    c = chain(*steps)
    error_cb = pipeline_error_handler.si(version_id=version_id)
    c.apply_async(link_error=error_cb)
    log.info("regeneration_started", version_id=version_id, stages=[s.value for s in planned])
    return version_id


def _will_retry(task, exc: Exception, policy: dict[str, Any] | None = None) -> bool:  # type: ignore[no-untyped-def]
    """Mirrors what Celery's autoretry machinery is about to decide.

    Needed because a failure must only be reported to the user once the task has
    genuinely given up. Recording it on the first of four attempts would flash a
    failure in the UI that a retry then silently contradicts.
    """
    policy = policy or RETRY_KWARGS
    if isinstance(exc, policy["dont_autoretry_for"]):
        return False
    return int(getattr(task.request, "retries", 0)) < int(policy["max_retries"])


def _fail_feedback(story_id: str, feedback_id: str, exc: Exception) -> None:
    """Record an interpretation failure and hand the story back to the user.

    Runs in its own session: the failure path exists precisely because the main
    transaction rolled back, so anything written inside that transaction is gone.
    """
    try:
        with session_scope() as session:
            feedback = session.get(Feedback, feedback_id)
            if feedback is not None:
                feedback.status = FeedbackStatus.FAILED
                feedback.error = str(exc)[:2000]
                session.add(feedback)

            story = session.get(Story, story_id)
            # Only undo the `generating` the API set for this request. If a
            # regeneration is already running, leave it alone.
            if story and story.status == StoryStatus.GENERATING:
                story.status = StoryStatus.READY
                session.add(story)
            session.commit()
    except Exception:
        log.exception("feedback_failure_not_recorded", feedback_id=feedback_id)

    repo.publish(
        story_id,
        {
            "type": "feedback",
            "status": FeedbackStatus.FAILED.value,
            "feedback_id": feedback_id,
            "error": str(exc)[:500],
        },
    )


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
    try:
        return _interpret_feedback(story_id, version_id, user_id, feedback_id)
    except Exception as exc:
        if not _will_retry(self, exc):
            _fail_feedback(story_id, feedback_id, exc)
        raise


def _interpret_feedback(
    story_id: str, version_id: str, user_id: str, feedback_id: str
) -> str:
    from daastaan_contracts.models import RegenDirective

    with session_scope() as session:
        feedback = session.get(Feedback, feedback_id)
        if feedback is None:
            raise LookupError(f"feedback {feedback_id} not found")

        state = repo.load_state(session, version_id)
        gateway = ModelGateway(
            session,
            stage="feedback_interpreter",
            version_id=version_id,
            user_id=user_id,
            story_id=story_id,
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
        feedback.status = FeedbackStatus.APPLIED
        feedback.error = None
        story = session.get(Story, story_id)
        if story:
            story.current_version_id = child.id
            story.status = StoryStatus.GENERATING
        session.commit()
        new_version_id = child.id

    repo.publish(
        story_id,
        {
            "type": "feedback",
            "status": FeedbackStatus.APPLIED.value,
            "feedback_id": feedback_id,
            "version_id": new_version_id,
            "scope": scope.value,
            "target_stage": target_stage.value,
            "target_id": target_id,
        },
    )
    regenerate.si(
        story_id, new_version_id, user_id, [s.value for s in planned], scope.value,
        target_id, directive.instruction_delta,
    ).apply_async(queue=Queue.AGENTS.value)
    return new_version_id


# --- audio export ----------------------------------------------------------


@celery_app.task(name=TaskName.EXPORT_AUDIO.value, bind=True, **RETRY_KWARGS)
def export_audio(self, version_id: str, user_id: str, fmt: str) -> str:
    """Transcode the finished episode into a downloadable format.

    Runs on the assembly pool, which is where ffmpeg already lives and where
    concurrency is 1 - the right place for a CPU-bound encode. Results are stored
    as assets, so the second request for a format is a lookup rather than another
    encode.
    """
    if fmt not in AUDIO_EXPORTS:
        raise ValueError(f"unsupported export format: {fmt}")

    dedupe = f"{AssetKind.EPISODE_EXPORT.value}:{fmt}"

    with session_scope() as session:
        if existing := repo.find_asset(session, version_id=version_id, dedupe_key=dedupe):
            log.info("export_cached", version_id=version_id, fmt=fmt)
            return existing.id

        master = session.exec(
            select(MediaAsset).where(
                MediaAsset.version_id == version_id,
                MediaAsset.kind == AssetKind.FINAL_EPISODE,
            )
        ).first()
        if master is None:
            raise LookupError(f"no final episode for version {version_id}")
        master_key, duration_ms = master.object_key, master.duration_ms

    store = get_store()
    data = transcode(store.get(master_key), fmt)
    key = ids.object_key(version_id, AssetKind.EPISODE_EXPORT, ext=fmt)
    content_type = export_content_type(fmt)
    store.put(key, data, content_type)

    with session_scope() as session:
        asset = repo.record_asset(
            session,
            version_id=version_id,
            kind=AssetKind.EPISODE_EXPORT,
            dedupe_key=dedupe,
            object_key=key,
            content_type=content_type,
            size_bytes=len(data),
            duration_ms=duration_ms,
        )
        session.commit()
        asset_id = asset.id

    log.info("export_ready", version_id=version_id, fmt=fmt, bytes=len(data))
    return asset_id


@celery_app.task(name=TaskName.EXPORT_BGM.value, bind=True, **RETRY_KWARGS)
def export_bgm(self, version_id: str, user_id: str, fmt: str) -> str:  # type: ignore[no-untyped-def]
    """Transcode the generated music bed into a downloadable format.

    The BGM master is a lossless 44.1 kHz stereo WAV, so MP3/FLAC/M4A/Opus here
    are first-generation encodes from a lossless source — genuinely better than
    their episode-export equivalents. WAV is never transcoded; callers serve the
    stored music_bed asset directly (same as the episode master for MP3).

    Runs on the assembly queue alongside episode transcodes.
    """
    if fmt not in BGM_AUDIO_EXPORTS:
        raise ValueError(f"unsupported BGM export format: {fmt}")

    dedupe = f"{AssetKind.BGM_EXPORT.value}:{fmt}"

    with session_scope() as session:
        if existing := repo.find_asset(session, version_id=version_id, dedupe_key=dedupe):
            log.info("bgm_export_cached", version_id=version_id, fmt=fmt)
            return existing.id

        master = session.exec(
            select(MediaAsset).where(
                MediaAsset.version_id == version_id,
                MediaAsset.kind == AssetKind.MUSIC_BED,
            )
        ).first()
        if master is None:
            raise LookupError(f"no music bed for version {version_id}")
        master_key, duration_ms = master.object_key, master.duration_ms

    store = get_store()
    data = transcode_bgm(store.get(master_key), fmt)
    key = ids.object_key(version_id, AssetKind.BGM_EXPORT, ext=fmt)
    content_type = bgm_content_type(fmt)
    store.put(key, data, content_type)

    with session_scope() as session:
        asset = repo.record_asset(
            session,
            version_id=version_id,
            kind=AssetKind.BGM_EXPORT,
            dedupe_key=dedupe,
            object_key=key,
            content_type=content_type,
            size_bytes=len(data),
            duration_ms=duration_ms,
        )
        session.commit()
        asset_id = asset.id

    log.info("bgm_export_ready", version_id=version_id, fmt=fmt, bytes=len(data))
    return asset_id


# --- document ingest -------------------------------------------------------


@celery_app.task(name=TaskName.INGEST_EXTRACT.value, bind=True, **INGEST_RETRY_KWARGS)
def ingest_extract(self, ingest_id: str, user_id: str) -> str:
    """Uploaded file -> reviewable story text.

    Runs on the agents pool. OCR is CPU-bound and that pool has four slots, so a
    handful of concurrent scans will saturate a small host; the page cap in
    `ingest.MAX_OCR_PAGES` is what keeps any single job bounded.
    """
    from . import ingest as extractor

    with session_scope() as session:
        job = session.get(IngestJob, ingest_id)
        if job is None:
            raise LookupError(f"ingest {ingest_id} not found")
        # Read what the rest of the task needs while the row is still attached.
        object_key, content_type = job.object_key, job.content_type
        job.status = IngestStatus.EXTRACTING
        session.commit()

    try:
        extraction = extractor.extract(get_store().get(object_key), content_type)

        with session_scope() as session:
            job = session.get(IngestJob, ingest_id)
            job.method = extraction.method.value
            job.page_count = extraction.page_count
            job.raw_chars = len(extraction.text)
            job.status = IngestStatus.CLEANING
            session.commit()

        cleaned, hints = _clean_document(extraction, user_id=user_id)

        with session_scope() as session:
            job = session.get(IngestJob, ingest_id)
            job.cleaned_text = cleaned
            job.title_hint, job.genre_hint, job.notes = hints
            job.status = IngestStatus.READY
            job.finished_at = _utcnow()
            session.commit()

        log.info(
            "ingest_ready",
            ingest_id=ingest_id,
            method=extraction.method.value,
            raw_chars=len(extraction.text),
            cleaned_chars=len(cleaned),
        )
        return ingest_id

    except Exception as exc:
        if _will_retry(self, exc, INGEST_RETRY_KWARGS):
            raise
        _fail_ingest(ingest_id, exc)
        raise


def _clean_document(
    extraction: "IngestExtraction", *, user_id: str
) -> tuple[str, tuple[str | None, str | None, str | None]]:
    from daastaan_contracts import StoryCleanupOutput

    with session_scope() as session:
        gateway = ModelGateway(
            session, stage="document_ingest", version_id=None, user_id=user_id
        )
        # Extracted text is exactly as untrusted as text a user types, and this
        # is the first model to see it. Moderation reads a prefix: the endpoint
        # has its own size limit and a whole book would cost more to screen than
        # the screening is worth.
        gateway.moderate(extraction.text[:MODERATION_SAMPLE_CHARS])
        result = gateway.structured(
            schema=StoryCleanupOutput,
            system=prompts.story_cleanup_prompt(max_chars=limits.MAX_STORY_INPUT_CHARS),
            user_content=(
                f"Extraction method: {extraction.method.value}. "
                f"Pages: {extraction.page_count}.\n\n"
                f"<document>\n{extraction.text[:MAX_CLEANUP_INPUT_CHARS]}\n</document>"
            ),
            kind="reasoning",
            temperature=0.2,
        )
        session.commit()

    cleaned = result.cleaned_text.strip()[: limits.MAX_STORY_INPUT_CHARS]
    if len(cleaned) < 20:
        raise ExtractionError(
            "No story could be found in that file. Check the upload, or paste the "
            "text in directly."
        )

    # The model is asked to pass prose through, not summarise it. When it ignores
    # that on a source that had room to spare, the story reaches the script stage
    # as a synopsis with its dialogue already gone - and nothing downstream can
    # tell that apart from a genuinely short story. Surface it here.
    if (
        len(extraction.text) > limits.MAX_STORY_INPUT_CHARS
        and len(cleaned) < limits.MAX_STORY_INPUT_CHARS * 0.4
    ):
        log.warning(
            "ingest.cleanup_suspiciously_short",
            raw_chars=len(extraction.text),
            cleaned_chars=len(cleaned),
            budget=limits.MAX_STORY_INPUT_CHARS,
            method=extraction.method.value,
        )
    return cleaned, (
        result.title_hint.strip()[:120] or None,
        result.genre_hint.strip()[:60] or None,
        result.notes.strip()[:500] or None,
    )


def _fail_ingest(ingest_id: str, exc: Exception) -> None:
    """Record the reason on the job so the compose form can show it.

    Extraction messages are written to be read by the person who uploaded the
    file. Anything else is replaced, because an OpenAI or database error should
    not be rendered into a form field.
    """
    friendly = (
        str(exc)
        if isinstance(exc, ExtractionError | ModerationBlocked)
        else "That file could not be processed. Try another file, or paste the text in directly."
    )
    try:
        with session_scope() as session:
            job = session.get(IngestJob, ingest_id)
            if job:
                job.status = IngestStatus.FAILED
                job.error = friendly[:500]
                job.finished_at = _utcnow()
                session.commit()
    except Exception:
        log.exception("ingest_failure_not_recorded", ingest_id=ingest_id)


def _resolve_target(state: StoryState, scope: Scope, target_id: str | None) -> str | None:
    """A target the model names must exist in the current state. Unknown ids are
    dropped rather than passed along."""
    if scope in {Scope.FULL_STORY, Scope.MUSIC} or not target_id:
        return None
    lookup = {
        Scope.LINE: state.line_by_id,
        Scope.CHARACTER: state.character_by_id,
        Scope.SCENE: state.scene_by_id,
    }[scope]
    return target_id if lookup(target_id) is not None else None
