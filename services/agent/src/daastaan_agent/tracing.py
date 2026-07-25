"""Tracing setup for the worker.

Langfuse is the live demo artifact - per-agent prompt, output, latency, and cost.
MLflow records the run against Databricks. Both are optional: if the keys are not
set the calls become no-ops, so tracing can never be the reason a demo fails.

Per the data-handling rule, spans carry metadata (stage, model, tokens, timings)
rather than raw story or feedback text. The content itself stays in the database
behind the same access controls as everything else.
"""

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import structlog
from daastaan_common import get_settings

log = structlog.get_logger(__name__)

_initialized = False


# --- pipeline context -----------------------------------------------------

@dataclass
class PipelineContext:
    pipeline_run_id: str | None = None
    story_id: str | None = None
    version_id: str | None = None
    stage_timings: dict[str, float] = field(default_factory=dict)


pipeline_context: ContextVar[PipelineContext | None] = ContextVar(
    "pipeline_context", default=None
)


def set_pipeline_context(
    *,
    pipeline_run_id: str | None = None,
    story_id: str | None = None,
    version_id: str | None = None,
) -> PipelineContext:
    ctx = PipelineContext(
        pipeline_run_id=pipeline_run_id,
        story_id=story_id,
        version_id=version_id,
    )
    pipeline_context.set(ctx)
    return ctx


def get_pipeline_context() -> PipelineContext | None:
    return pipeline_context.get()


def clear_pipeline_context() -> None:
    pipeline_context.set(None)


# --- init -----------------------------------------------------------------

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
            mlflow.langchain.autolog(run_tracer_inline=True)
            log.info("mlflow_ready", experiment=settings.mlflow_experiment)
        except Exception:
            log.warning("mlflow_init_failed", exc_info=True)


# --- MLflow run lifecycle -------------------------------------------------

def start_mlflow_run(
    *, version_id: str, story_id: str
) -> str | None:
    """Start an MLflow run and return its run_id, or None if MLflow is disabled."""
    settings = get_settings()
    if not settings.mlflow_enabled:
        return None
    try:
        import mlflow

        run = mlflow.start_run(
            run_name=f"pipeline-{version_id[:8]}",
            tags={
                "version_id": version_id,
                "story_id": story_id,
            },
        )
        return run.info.run_id
    except Exception:
        log.warning("mlflow_start_failed", exc_info=True)
        return None


def end_mlflow_run(
    *,
    status: str = "FINISHED",
    metrics: dict[str, float] | None = None,
) -> None:
    """End the current MLflow run, logging final metrics."""
    settings = get_settings()
    if not settings.mlflow_enabled:
        return
    try:
        import mlflow

        if metrics:
            mlflow.log_metrics(metrics)
        mlflow.end_run(status=status)
    except Exception:
        log.warning("mlflow_end_failed", exc_info=True)


def log_mlflow_stage(stage: str, duration_s: float) -> None:
    """Log a stage duration metric to the active MLflow run."""
    settings = get_settings()
    if not settings.mlflow_enabled:
        return
    try:
        import mlflow

        mlflow.log_metric(f"stage_{stage}_duration_s", round(duration_s, 3))
    except Exception:
        log.warning("mlflow_log_metric_failed", stage=stage, exc_info=True)


# --- Langfuse spans ------------------------------------------------------

def langfuse_span(
    *,
    name: str,
    metadata: dict[str, Any] | None = None,
):
    """Create a Langfuse span as a context manager. No-op if Langfuse is off."""
    settings = get_settings()
    if settings.langfuse_enabled and settings.langfuse_public_key:
        try:
            from langfuse import get_client

            client = get_client()
            ctx = get_pipeline_context()
            trace_id = ctx.pipeline_run_id if ctx else None
            span = client.span(
                name=name,
                trace_id=trace_id,
                metadata=metadata or {},
            )
            return _LangfuseSpanCtx(span)
        except Exception:
            log.debug("langfuse_span_create_failed", name=name, exc_info=True)
    return _NoopSpanCtx()


class _LangfuseSpanCtx:
    def __init__(self, span: Any) -> None:
        self.span = span
        self._start: float = 0.0

    def __enter__(self) -> "_LangfuseSpanCtx":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        duration_ms = (time.monotonic() - self._start) * 1000
        try:
            self.span.end(
                metadata={"duration_ms": round(duration_ms, 1)},
                level="ERROR" if exc_type else "DEFAULT",
            )
        except Exception:  # noqa: S110
            pass  # Tracing must never break the pipeline

    def update(self, **kwargs: Any) -> None:
        try:
            self.span.update(**kwargs)
        except Exception:  # noqa: S110
            pass  # Tracing must never break the pipeline


class _NoopSpanCtx:
    def __enter__(self) -> "_NoopSpanCtx":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def update(self, **kwargs: Any) -> None:
        pass


# --- metadata whitelist ---------------------------------------------------

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
