"""Celery worker entry point.

Start one pool per queue so a long ffmpeg assembly never sits behind a backlog of
TTS calls:

    celery -A daastaan_agent.worker worker -Q agents   -c 4
    celery -A daastaan_agent.worker worker -Q media    -c 6
    celery -A daastaan_agent.worker worker -Q assembly -c 1

Media concurrency is the per-line TTS fan-out width. Assembly stays at 1 because
ffmpeg is CPU-bound and parallel jobs would only contend.
"""

from daastaan_common import celery_app, configure_logging

from . import tasks  # noqa: F401  (import registers the tasks with the app)
from .tracing import init_tracing

configure_logging("worker")
init_tracing()

__all__ = ["celery_app"]
