"""Database engine and sessions.

Lakebase is Postgres, so the only difference from the local Compose database is
authentication: its OAuth tokens expire hourly, so a fresh one is minted for every
new pooled connection through SQLAlchemy's `do_connect` event. `pool_pre_ping`
transparently replaces connections dropped by Lakebase scale-to-zero, which also
means the first query after an idle period may take a few seconds.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import structlog
from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

from .settings import get_settings

log = structlog.get_logger(__name__)


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    engine = create_engine(
        settings.sqlalchemy_url(),
        echo=settings.db_echo,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=5,
    )
    if settings.db_backend == "lakebase":
        _attach_lakebase_token_rotation(engine)
    return engine


def _attach_lakebase_token_rotation(engine: Engine) -> None:
    from databricks.sdk import WorkspaceClient

    settings = get_settings()
    workspace = WorkspaceClient()

    @event.listens_for(engine, "do_connect")
    def _inject_oauth_token(dialect, conn_rec, cargs, cparams):  # type: ignore[no-untyped-def]
        credential = workspace.postgres.generate_database_credential(
            endpoint=settings.lakebase_endpoint
        )
        cparams["password"] = credential.token

    log.info("lakebase_token_rotation_enabled", endpoint=settings.lakebase_endpoint)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for workers and scripts."""
    with Session(get_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Commit explicitly in the route that mutates."""
    with Session(get_engine()) as session:
        yield session


def init_db() -> None:
    """Create any missing tables.

    Idempotent and good enough for a hackathon; swap in Alembic if the schema
    starts changing after data exists.
    """
    from . import models  # noqa: F401  (import registers the table metadata)

    SQLModel.metadata.create_all(get_engine())
    log.info("schema_ready")
