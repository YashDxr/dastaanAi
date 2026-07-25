"""Tracing setup for the worker.

Langfuse is the live demo artifact - per-agent prompt, output, latency, and cost.
MLflow records the run against Databricks. Both are optional: if the keys are not
set the calls become no-ops, so tracing can never be the reason a demo fails.

Per the data-handling rule, spans carry metadata (stage, model, tokens, timings)
rather than raw story or feedback text. The content itself stays in the database
behind the same access controls as everything else.
"""

from typing import Any

import structlog
from daastaan_common import get_settings

log = structlog.get_logger(__name__)

_initialized = False


def init_tracing() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True

    settings = get_settings()

    if settings.langfuse_enabled and settings.langfuse_public_key:
        try:
            from langfuse import get_client

            get_client()
            log.info("langfuse_ready", host=settings.langfuse_host)
        except Exception:
            log.warning("langfuse_init_failed", exc_info=True)

    if settings.mlflow_enabled:
        try:
            import mlflow

            mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
            mlflow.set_experiment(settings.mlflow_experiment)
            # Required, not optional: the pipeline mixes autolog with manual
            # spans in async code, and without inline execution LangChain runs
            # the tracer on another thread, detaching manual spans into their
            # own root traces.
            mlflow.langchain.autolog(run_tracer_inline=True)
            log.info("mlflow_ready", experiment=settings.mlflow_experiment)
        except Exception:
            log.warning("mlflow_init_failed", exc_info=True)


def trace_metadata(**fields: Any) -> dict[str, Any]:
    """Whitelist of fields safe to attach to a span."""
    allowed = {
        "stage",
        "model",
        "version_id",
        "story_id",
        "input_tokens",
        "output_tokens",
        "duration_ms",
        "cost_usd",
        "line_count",
        "scene_count",
        "attempt",
    }
    return {k: v for k, v in fields.items() if k in allowed}
