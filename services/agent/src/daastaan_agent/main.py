"""Agent service HTTP surface.

The workers do the real work off the Celery queues; this service exists so the
pipeline is reachable directly. That matters for three things: a health endpoint
the API and Compose can check, a synchronous debug run for iterating on prompts
without a broker, and a future HTTP dispatch path if the API is ever deployed
somewhere it cannot share a broker with the workers.

It is an internal service. Do not expose it publicly without putting
authentication in front of it.
"""

from contextlib import asynccontextmanager

import structlog
from daastaan_common import configure_logging, get_settings, session_scope
from daastaan_common.middleware import RequestIdMiddleware
from daastaan_contracts import StageName, StoryState
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from . import repo
from .graph import run_agent_stages
from .nodes import STAGE_NODES
from .tracing import init_tracing

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging("agent")
    init_tracing()
    log.info("agent_started", env=get_settings().app_env)
    yield


app = FastAPI(title="Daastaan Agent Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)


class DebugRunRequest(BaseModel):
    version_id: str
    user_id: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/stages")
def stages() -> dict[str, list[str]]:
    """What this build can execute. Useful when the API and agent are deployed
    separately and you need to confirm they agree on the registry."""
    return {
        "all": [stage.value for stage in StageName],
        "implemented": [stage.value for stage in STAGE_NODES],
    }


@app.post("/internal/run-stages")
def run_stages_now(body: DebugRunRequest) -> dict[str, object]:
    """Run the reasoning stages in-process and synchronously.

    Skips the queue entirely, so it is the quickest way to test prompt changes.
    Media generation and assembly still go through Celery.
    """
    with session_scope() as session:
        try:
            state = repo.load_state(session, body.version_id)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    # Each graph node opens its own session_scope().
    result: StoryState = run_agent_stages(state, body.user_id)

    with session_scope() as session:
        repo.save_state(session, result)

    return {
        "version_id": result.version_id,
        "title": result.title,
        "scenes": len(result.scenes),
        "characters": len(result.characters),
        "lines": len(result.lines),
    }
