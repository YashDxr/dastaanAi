"""Main API service.

Everything is mounted under `/api` so the two React apps can proxy a single
prefix in development and sit behind one origin in production, which keeps the
session cookie same-origin and avoids CORS entirely in the common case.
"""

from contextlib import asynccontextmanager

import structlog
from daastaan_common import configure_logging, get_settings, init_db
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .routers import admin, auth, feedback, health, media, progress, stories

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging("api")
    init_db()
    log.info("api_started", env=get_settings().app_env)
    yield


app = FastAPI(
    title="Daastaan AI API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace to a client. The detail goes to the logs."""
    log.exception("unhandled_error", path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "internal server error"},
    )


for router in (health.router, auth.router, stories.router, feedback.router, media.router,
               admin.router):
    app.include_router(router, prefix="/api")

# WebSocket paths are not prefixed: the route already carries its own /ws prefix.
app.include_router(progress.router)
