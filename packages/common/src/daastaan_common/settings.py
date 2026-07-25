"""Configuration for every service in the monorepo.

The `*_backend` switches are what let Databricks be additive rather than
load-bearing: set them to `local` and the whole stack runs on the Compose
Postgres and a local directory with no Databricks account involved.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Not a secret: a deliberately recognisable placeholder that the validator below
# refuses to accept outside local development.
DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-before-deploying"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: Literal["local", "staging", "prod"] = "local"
    log_level: str = "INFO"
    # JSON logs are required for Loki/Grafana field search (request_id, etc.).
    log_json: bool = False

    # --- database ---------------------------------------------------------
    # Where Postgres runs for local development. Orthogonal to `db_backend`
    # (which switches local Postgres vs Databricks Lakebase).
    #   docker → Compose starts the `postgres` service (make up)
    #   host   → use a Postgres already listening on your machine; set
    #            DATABASE_URL to host.docker.internal from containers
    postgres_mode: Literal["docker", "host"] = "docker"

    # `local` points at Postgres (Compose or host). `lakebase` swaps in a
    # Databricks OAuth token per connection; see db.py.
    db_backend: Literal["local", "lakebase"] = "local"
    database_url: str = "postgresql+psycopg://daastaan:daastaan@localhost:5432/daastaan"
    db_echo: bool = False

    lakebase_host: str | None = None
    lakebase_database: str = "databricks_postgres"
    lakebase_user: str | None = None
    lakebase_port: int = 5432
    # projects/{project-id}/branches/{branch-id}/endpoints/{endpoint-id}
    lakebase_endpoint: str | None = None

    # --- object storage ---------------------------------------------------
    storage_backend: Literal["local", "uc_volume"] = "local"
    local_media_dir: str = "/data/media"
    uc_volume_path: str | None = None  # /Volumes/<catalog>/<schema>/<volume>

    # --- broker -----------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- response cache ---------------------------------------------------
    # Content-addressed and global: the key covers every input that determines
    # the output, so a hit can only be served to a byte-identical request.
    # Turn it off to force fresh generations while tuning prompts.
    cache_enabled: bool = True
    cache_ttl_seconds: int = 60 * 60 * 24 * 14

    # --- OpenAI -----------------------------------------------------------
    openai_api_key: str | None = None
    model_reasoning: str = "gpt-4o"
    model_light: str = "gpt-4o-mini"
    model_tts: str = "gpt-4o-mini-tts"
    model_image: str = "gpt-image-1"
    model_moderation: str = "omni-moderation-latest"

    # --- Databricks (optional everywhere) ---------------------------------
    databricks_host: str | None = None
    databricks_token: str | None = None

    mlflow_enabled: bool = False
    mlflow_tracking_uri: str = "databricks"
    mlflow_experiment: str = "/Shared/daastaan"

    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- auth -------------------------------------------------------------
    # 32 bytes is the floor for HMAC-SHA256; PyJWT warns below it, and a short
    # secret is a forgeable session token. The default is long enough to satisfy
    # that but is rejected outside local by the validator below.
    jwt_secret: str = Field(default=DEV_JWT_SECRET, min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 60 * 60 * 12
    cookie_name: str = "daastaan_session"
    cookie_secure: bool = False

    # --- service wiring ---------------------------------------------------
    api_base_url: str = "http://localhost:8000"
    agent_base_url: str = "http://localhost:8100"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:5174"]

    @model_validator(mode="after")
    def _reject_insecure_defaults_outside_local(self) -> "Settings":
        """Fail at startup rather than shipping a known-public signing key.

        Anyone with the repo could mint an admin session if this default reached
        a deployed environment, so it is a hard error, not a warning.
        """
        if self.app_env == "local":
            return self
        problems = []
        if self.jwt_secret == DEV_JWT_SECRET:
            problems.append("JWT_SECRET is still the development default")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true outside local")
        if problems:
            raise ValueError(f"unsafe configuration for app_env={self.app_env}: " +
                             "; ".join(problems))
        return self

    def sqlalchemy_url(self) -> str:
        """Lakebase is plain Postgres, so only the DSN differs. The password is
        injected per connection by the `do_connect` listener in db.py."""
        if self.db_backend == "local":
            return self.database_url
        missing = [
            name
            for name in ("lakebase_host", "lakebase_user", "lakebase_endpoint")
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError(f"db_backend=lakebase requires: {', '.join(missing)}")
        return (
            f"postgresql+psycopg://{self.lakebase_user}@{self.lakebase_host}"
            f":{self.lakebase_port}/{self.lakebase_database}?sslmode=require"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
