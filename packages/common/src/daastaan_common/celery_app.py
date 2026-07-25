"""The single Celery application, shared by producer and consumer.

The API imports this to dispatch by task *name* and never imports the agent
service, which keeps LangGraph, OpenAI, and ffmpeg out of the API image while
both sides still agree on the wire format via `daastaan_contracts.TaskName`.
"""

from celery import Celery
from daastaan_contracts import Queue, TaskName

from .request_id import RequestIdTask
from .settings import get_settings


def create_celery(name: str = "daastaan") -> Celery:
    settings = get_settings()
    app = Celery(name, broker=settings.redis_url, backend=settings.redis_url)
    # Every task binds/propagates X-Request-ID via Celery headers.
    app.Task = RequestIdTask

    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Redelivery on worker loss is safe because every paid call is guarded by
        # an idempotency check against media_assets before it runs.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_track_started=True,
        # Long tasks: never let a worker hoard queued work it cannot start.
        worker_prefetch_multiplier=1,
        task_time_limit=900,
        task_soft_time_limit=840,
        result_expires=60 * 60 * 24,
        broker_transport_options={"visibility_timeout": 3600},
        task_default_queue=Queue.AGENTS.value,
        # Stage tasks pick their queue at send time from STAGE_QUEUE, since it
        # depends on which stage is running. These are fixed.
        task_routes={
            TaskName.TTS_LINE.value: {"queue": Queue.MEDIA.value},
            TaskName.GEN_IMAGE.value: {"queue": Queue.MEDIA.value},
            TaskName.GEN_MUSIC.value: {"queue": Queue.MUSIC.value},
            TaskName.ASSEMBLE.value: {"queue": Queue.ASSEMBLY.value},
            TaskName.INTERPRET_FEEDBACK.value: {"queue": Queue.AGENTS.value},
            # A read-only lightweight-model review, intentionally not a
            # pipeline stage or a media task.
            TaskName.CONSISTENCY_CHECK.value: {"queue": Queue.AGENTS.value},
            TaskName.RUN_PIPELINE.value: {"queue": Queue.AGENTS.value},
            TaskName.REGENERATE.value: {"queue": Queue.AGENTS.value},
            TaskName.INGEST_EXTRACT.value: {"queue": Queue.AGENTS.value},
            TaskName.COMPOSE_VIDEO.value: {"queue": Queue.ASSEMBLY.value},
            # Transcoding belongs wherever ffmpeg already runs.
            TaskName.EXPORT_AUDIO.value: {"queue": Queue.ASSEMBLY.value},
        },
    )
    return app


celery_app = create_celery()
