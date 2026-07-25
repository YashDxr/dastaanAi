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


# Columns added after the first deploy. `create_all` creates missing *tables*
# and silently ignores missing *columns* on tables that already exist, so a
# column added later never reaches a database that has data in it. Postgres
# supports `ADD COLUMN IF NOT EXISTS`, which makes replaying the whole list on
# every boot both safe and cheap - far less machinery than Alembic for a schema
# that only grows.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("feedback", "status", "VARCHAR NOT NULL DEFAULT 'pending'"),
    ("feedback", "error", "VARCHAR"),
    ("cost_ledger", "cache_hit", "BOOLEAN NOT NULL DEFAULT FALSE"),
)


def _apply_column_migrations() -> None:
    from sqlalchemy import text

    with get_engine().begin() as connection:
        for table, column, ddl in _ADDED_COLUMNS:
            connection.execute(
                text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {ddl}')
            )


def init_db() -> None:
    """Create any missing tables, then backfill any columns added since.

    Idempotent and good enough for a hackathon; swap in Alembic if the schema
    starts changing in ways that need real up/down migrations.
    """
    from . import models  # noqa: F401  (import registers the table metadata)

    SQLModel.metadata.create_all(get_engine())
    _apply_column_migrations()
    log.info("schema_ready")
