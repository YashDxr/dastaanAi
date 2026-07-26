"""Handing work to the agent service.

Dispatch is by task *name*, never by importing the task function. That is what
lets the API image stay free of LangGraph, OpenAI, and ffmpeg while both services
still agree on the wire format through `daastaan_contracts.TaskName`.
"""

import structlog
from daastaan_common import celery_app
from daastaan_common.request_id import CELERY_REQUEST_ID_KEY, current_request_id
from daastaan_contracts import Queue, StageName, TaskName

log = structlog.get_logger(__name__)


def _task_headers() -> dict[str, str]:
    request_id = current_request_id()
    return {CELERY_REQUEST_ID_KEY: request_id} if request_id else {}


def dispatch_pipeline(*, story_id: str, version_id: str, user_id: str) -> str:
    task = celery_app.send_task(
        TaskName.RUN_PIPELINE.value,
        kwargs={"story_id": story_id, "version_id": version_id, "user_id": user_id},
        queue=Queue.AGENTS.value,
        headers=_task_headers(),
    )
    log.info("dispatched_pipeline", story_id=story_id, version_id=version_id, task_id=task.id)
    return task.id


def dispatch_regeneration(
    *,
    story_id: str,
    version_id: str,
    user_id: str,
    stages: list[StageName],
    scope: str,
    target_id: str | None,
    instruction_delta: str,
) -> str:
    task = celery_app.send_task(
        TaskName.REGENERATE.value,
        kwargs={
            "story_id": story_id,
            "version_id": version_id,
            "user_id": user_id,
            "stages": [stage.value for stage in stages],
            "scope": scope,
            "target_id": target_id,
            "instruction_delta": instruction_delta,
        },
        queue=Queue.AGENTS.value,
        headers=_task_headers(),
    )
    log.info(
        "dispatched_regeneration",
        story_id=story_id,
        version_id=version_id,
        stages=[stage.value for stage in stages],
        task_id=task.id,
    )
    return task.id


def dispatch_ingest(*, ingest_id: str, user_id: str) -> str:
    task = celery_app.send_task(
        TaskName.INGEST_EXTRACT.value,
        kwargs={"ingest_id": ingest_id, "user_id": user_id},
        queue=Queue.AGENTS.value,
        headers=_task_headers(),
    )
    log.info("dispatched_ingest", ingest_id=ingest_id, task_id=task.id)
    return task.id


def dispatch_audio_export(*, version_id: str, user_id: str, fmt: str) -> str:
    task = celery_app.send_task(
        TaskName.EXPORT_AUDIO.value,
        kwargs={"version_id": version_id, "user_id": user_id, "fmt": fmt},
        queue=Queue.ASSEMBLY.value,
        headers=_task_headers(),
    )
    log.info("dispatched_export", version_id=version_id, fmt=fmt, task_id=task.id)
    return task.id


def dispatch_bgm_export(*, version_id: str, user_id: str, fmt: str) -> str:
    task = celery_app.send_task(
        TaskName.EXPORT_BGM.value,
        kwargs={"version_id": version_id, "user_id": user_id, "fmt": fmt},
        queue=Queue.ASSEMBLY.value,
        headers=_task_headers(),
    )
    log.info("dispatched_bgm_export", version_id=version_id, fmt=fmt, task_id=task.id)
    return task.id


def dispatch_video_edit_render(*, edit_id: str, user_id: str) -> str:
    task = celery_app.send_task(
        TaskName.RENDER_VIDEO_EDIT.value,
        kwargs={"edit_id": edit_id, "user_id": user_id},
        queue=Queue.ASSEMBLY.value,
        headers=_task_headers(),
    )
    log.info("dispatched_video_edit_render", edit_id=edit_id, task_id=task.id)
    return task.id


def dispatch_feedback_interpretation(
    *, story_id: str, version_id: str, user_id: str, feedback_id: str
) -> str:
    task = celery_app.send_task(
        TaskName.INTERPRET_FEEDBACK.value,
        kwargs={
            "story_id": story_id,
            "version_id": version_id,
            "user_id": user_id,
            "feedback_id": feedback_id,
        },
        queue=Queue.AGENTS.value,
        headers=_task_headers(),
    )
    log.info("dispatched_feedback", feedback_id=feedback_id, task_id=task.id)
    return task.id


def dispatch_consistency_check(*, check_id: str, user_id: str) -> str:
    """Queue a read-only editorial review on the lightweight agents worker."""
    task = celery_app.send_task(
        TaskName.CONSISTENCY_CHECK.value,
        kwargs={"check_id": check_id, "user_id": user_id},
        queue=Queue.AGENTS.value,
        headers=_task_headers(),
    )
    log.info("dispatched_consistency_check", check_id=check_id, task_id=task.id)
    return task.id
