"""LangGraph wiring for the reasoning stages.

Now the production execution path: ``run_pipeline`` in ``tasks.py`` calls
``run_agent_stages`` to execute every reasoning node in-process inside a single
Celery task.  This eliminates seven sequential task dispatches through the
broker while keeping identical node functions for both the full run and scoped
regenerations (which still use the per-stage Celery path).

Stages 5 (emotion_tagging) and 6 (narrator_persona) have no data dependency -
one reads lines, the other reads mood/arc/setting - so they run in parallel via
a LangGraph fan-out/fan-in, saving one LLM round-trip per pipeline run.
"""

import operator
import time
from typing import Annotated, Any

import structlog
from daastaan_contracts import JobStatus, StageName, StoryState
from langgraph.graph import END, START, StateGraph
from sqlmodel import Session
from typing_extensions import TypedDict

from . import repo
from .gateway import ModelGateway
from .nodes import STAGE_NODES
from .tracing import get_pipeline_context, log_mlflow_stage

log = structlog.get_logger(__name__)


class GraphState(TypedDict, total=False):
    """LangGraph state with reducers for fields written by parallel branches.

    ``completed_stages`` uses ``operator.add`` so that both parallel nodes
    (emotion_tagging and narrator_persona) can independently append their stage
    without conflicting on a LastValue channel.

    All other fields use the default LastValue semantics, which is correct
    because only one branch writes each field.
    """
    story_id: str
    version_id: str
    user_id: str
    raw_text: str
    genre_hint: str | None
    mood: Any
    title: str | None
    arc_summary: str | None
    setting: str | None
    scenes: list[Any]
    characters: list[Any]
    lines: list[Any]
    narrator_persona: Any
    voice_map: list[Any]
    audio_assets: list[Any]
    image_assets: list[Any]
    final_episode_key: str | None
    regen: Any
    completed_stages: Annotated[list[StageName], operator.add]


# Fields that parallel branches should not both write (they would conflict
# on LangGraph's LastValue channel).  Only the fields a node *actually
# mutates* are returned in its diff dict.
_PARALLEL_SAFE_FIELDS: dict[StageName, set[str]] = {
    StageName.EMOTION_TAGGING: {"lines", "completed_stages"},
    StageName.NARRATOR_PERSONA: {"narrator_persona", "completed_stages"},
}


def build_graph(session: Session, user_id: str) -> Any:
    """Compile the agent stages into a graph with parallel emotion/narrator.

    Session and user are bound at build time because a LangGraph node only
    receives state; the surrounding infrastructure is closed over instead.
    """
    builder = StateGraph(GraphState)

    def make_node(stage: StageName):  # type: ignore[no-untyped-def]
        node = STAGE_NODES[stage]

        def run(state: dict[str, Any]) -> dict[str, Any]:
            story_state = StoryState.model_validate(state)
            job = repo.start_job(
                session,
                story_id=story_state.story_id,
                version_id=story_state.version_id,
                stage=stage,
            )
            t0 = time.monotonic()
            try:
                gateway = ModelGateway(
                    session, stage=stage.value, version_id=story_state.version_id, user_id=user_id
                )
                updated = node(session, story_state, gateway)

                duration_s = time.monotonic() - t0
                repo.finish_job(session, job, status=JobStatus.SUCCEEDED)
                repo.save_state(session, updated)
                log.info(
                    "stage_completed",
                    stage=stage.value,
                    duration_ms=round(duration_s * 1000),
                )
                log_mlflow_stage(stage.value, duration_s)

                ctx = get_pipeline_context()
                if ctx:
                    ctx.stage_timings[stage.value] = duration_s

                full = updated.model_dump(mode="python")
                # For parallel nodes, only return the fields they changed so
                # LangGraph does not see conflicting writes on LastValue channels.
                # completed_stages uses an add reducer, so return only the new stage.
                safe = _PARALLEL_SAFE_FIELDS.get(stage)
                if safe is not None:
                    diff = {k: v for k, v in full.items() if k in safe}
                    diff["completed_stages"] = [stage]
                    return diff
                # For sequential nodes, return full state but replace
                # completed_stages with only the new entry (reducer will add it).
                full["completed_stages"] = [stage]
                return full
            except Exception as exc:
                duration_s = time.monotonic() - t0
                repo.finish_job(session, job, status=JobStatus.FAILED, error=str(exc))
                log.error(
                    "stage_failed",
                    stage=stage.value,
                    duration_ms=round(duration_s * 1000),
                    error=str(exc),
                )
                raise

        run.__name__ = stage.value
        return run

    # Stages 1-4: linear
    linear_prefix = [
        StageName.MOOD_CLASSIFICATION,
        StageName.STORY_UNDERSTANDING,
        StageName.CHARACTER_REGISTRY,
        StageName.DIALOGUE_ATTRIBUTION,
    ]
    previous: str = START
    for stage in linear_prefix:
        builder.add_node(stage.value, make_node(stage))
        builder.add_edge(previous, stage.value)
        previous = stage.value

    # Stages 5+6: parallel (emotion_tagging and narrator_persona)
    builder.add_node(StageName.EMOTION_TAGGING.value, make_node(StageName.EMOTION_TAGGING))
    builder.add_node(StageName.NARRATOR_PERSONA.value, make_node(StageName.NARRATOR_PERSONA))
    builder.add_edge(previous, StageName.EMOTION_TAGGING.value)
    builder.add_edge(previous, StageName.NARRATOR_PERSONA.value)

    # Join node: both must complete before voice_assignment
    builder.add_node(StageName.VOICE_ASSIGNMENT.value, make_node(StageName.VOICE_ASSIGNMENT))
    builder.add_edge(StageName.EMOTION_TAGGING.value, StageName.VOICE_ASSIGNMENT.value)
    builder.add_edge(StageName.NARRATOR_PERSONA.value, StageName.VOICE_ASSIGNMENT.value)

    builder.add_edge(StageName.VOICE_ASSIGNMENT.value, END)
    return builder.compile()


def run_agent_stages(session: Session, state: StoryState, user_id: str) -> StoryState:
    graph = build_graph(session, user_id)
    # Convert StoryState to dict for the GraphState TypedDict input.
    # Initialize completed_stages as empty so the add reducer starts clean.
    input_state = state.model_dump(mode="python")
    input_state["completed_stages"] = []
    result = graph.invoke(input_state)
    return StoryState.model_validate(result)
