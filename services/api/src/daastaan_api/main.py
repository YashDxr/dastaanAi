"""Main API service.

Everything is mounted under `/api` so the two React apps can proxy a single
prefix in development and sit behind one origin in production, which keeps the
session cookie same-origin and avoids CORS entirely in the common case.
"""

from contextlib import asynccontextmanager
from typing import Any

import structlog
from daastaan_common import configure_logging, get_settings, init_db
from daastaan_common.middleware import RequestIdMiddleware
from daastaan_common.request_id import REQUEST_ID_HEADER, current_request_id
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from .routers import (
    admin,
    auth,
    cliffhanger,
    consistency,
    exports,
    feedback,
    health,
    ingest,
    media,
    progress,
    share,
    stories,
    story_genome,
    studio,
    video_editor,
    writers_room,
)

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging("api")
    init_db()
    log.info("api_started", env=get_settings().app_env, postgres_mode=get_settings().postgres_mode)
    yield


app = FastAPI(
    title="Daastaan AI API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

settings = get_settings()
# Request-id outermost so CORS and handlers all see the bound context.
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", REQUEST_ID_HEADER],
    # A media element only treats a response as seekable when it can read the
    # range headers back, which cross-origin it cannot unless they are exposed.
    expose_headers=[
        REQUEST_ID_HEADER,
        "Accept-Ranges",
        "Content-Range",
        "Content-Length",
        # Without this a cross-origin download saves under the asset id.
        "Content-Disposition",
    ],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace to a client. The detail goes to the logs."""
    request_id = getattr(request.state, "request_id", None) or current_request_id()
    log.exception("unhandled_error", path=request.url.path, request_id=request_id)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "internal server error", "request_id": request_id},
        headers={REQUEST_ID_HEADER: request_id} if request_id else {},
    )


for router in (
    health.router,
    auth.router,
    stories.router,
    feedback.router,
    consistency.router,
    media.router,
    admin.router,
    studio.router,
    ingest.router,
    exports.router,
    video_editor.router,
    share.router,
    writers_room.router,
    cliffhanger.router,
    story_genome.router,
    progress.sse_router,
):
    app.include_router(router, prefix="/api")

# WebSocket paths are not prefixed: the route already carries its own /ws prefix.
app.include_router(progress.router)


def custom_openapi() -> dict[str, Any]:
    """The generated document, plus the schemas FastAPI cannot infer.

    The progress event union is delivered as a stream of SSE frames rather than a
    response body, so no `response_model` describes it and it would otherwise be
    absent from the document - leaving the two React apps to hand-maintain a copy of
    every event shape. Merging it in here keeps Pydantic the single source of truth
    for the event contract as well as for requests and responses.

    Existing entries win, because the event models reference enums FastAPI has
    already published from other routes and those definitions are the same ones.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name, definition in progress.event_component_schemas().items():
        components.setdefault(name, definition)

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi  # type: ignore[method-assign]
