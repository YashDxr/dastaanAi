"""Request-id helpers shared by the API and Celery workers.

Every HTTP response echoes `X-Request-ID`. The same value is attached to Celery
task headers so worker logs for that request can be grepped (or searched in
Grafana/Loki) as one thread.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from celery import Task

REQUEST_ID_HEADER = "X-Request-ID"
CELERY_REQUEST_ID_KEY = "daastaan_request_id"


def new_request_id() -> str:
    return uuid.uuid4().hex


def bind_request_id(request_id: str) -> None:
    structlog.contextvars.bind_contextvars(request_id=request_id)


def clear_request_id() -> None:
    structlog.contextvars.unbind_contextvars("request_id")


def current_request_id() -> str | None:
    return structlog.contextvars.get_contextvars().get("request_id")


def ensure_request_id(existing: str | None = None) -> str:
    request_id = (existing or "").strip() or new_request_id()
    bind_request_id(request_id)
    return request_id


class RequestIdTask(Task):
    """Default Celery task base: bind request_id on run, propagate on spawn.

    Child signatures (chain / chord / group) inherit the header through
    `apply_async`, so a single browser click stays one searchable id across the
    API, agents, media, and assembly workers.
    """

    abstract = True

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        headers = getattr(self.request, "headers", None) or {}
        request_id = headers.get(CELERY_REQUEST_ID_KEY) or kwargs.get("request_id")
        if request_id:
            bind_request_id(str(request_id))
        try:
            return super().__call__(*args, **kwargs)
        finally:
            clear_request_id()

    def apply_async(self, args=None, kwargs=None, **options):  # type: ignore[no-untyped-def]
        headers = dict(options.get("headers") or {})
        request_id = current_request_id()
        if request_id and CELERY_REQUEST_ID_KEY not in headers:
            headers[CELERY_REQUEST_ID_KEY] = request_id
        options["headers"] = headers
        return super().apply_async(args=args, kwargs=kwargs, **options)
