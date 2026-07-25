"""Main API service.

Everything is mounted under `/api` so the two React apps can proxy a single
prefix in development and sit behind one origin in production, which keeps the
session cookie same-origin and avoids CORS entirely in the common case.
"""

from contextlib import asynccontextmanager

import structlog
from daastaan_common import configure_logging, get_settings, init_db
from daastaan_common.middleware import RequestIdMiddleware
from daastaan_common.request_id import REQUEST_ID_HEADER, current_request_id
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .routers import (
    admin,
    auth,
    exports,
    feedback,
    health,
    ingest,
    media,
    progress,
    stories,
    studio,
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


for router in (health.router, auth.router, stories.router, feedback.router, media.router,
               admin.router, studio.router, ingest.router, exports.router,
               progress.sse_router):
    app.include_router(router, prefix="/api")

# WebSocket paths are not prefixed: the route already carries its own /ws prefix.
app.include_router(progress.router)
