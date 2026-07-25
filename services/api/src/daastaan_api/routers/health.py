import redis
import structlog
from daastaan_common import get_settings
from fastapi import APIRouter
from sqlalchemy import text

from ..deps import SessionDep

log = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness only. Kept dependency-free so a database blip cannot make the
    container look dead to an orchestrator."""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(session: SessionDep) -> dict[str, object]:
    """Readiness. Reports each dependency separately so a failure points at the
    thing that is actually broken."""
    checks: dict[str, str] = {}

    try:
        session.exec(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"

    try:
        redis.from_url(get_settings().redis_url, socket_connect_timeout=2).ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"

    return {"ready": all(v == "ok" for v in checks.values()), "checks": checks}
