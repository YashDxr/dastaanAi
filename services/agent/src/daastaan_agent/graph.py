"""LangGraph wiring for the reasoning stages.

Production runs go through Celery, which distributes the same `STAGE_NODES` across
workers. This graph is the in-process path: it runs the identical node functions
end to end in one call, which makes it the fastest way to iterate on prompts, the
easiest thing to unit test, and a working fallback if the broker is unavailable
during a demo.

Both paths share the node registry, so there is exactly one implementation of each
stage no matter how it is executed.
"""

from typing import Any

import structlog
from daastaan_contracts import AGENT_STAGES, StageName, StoryState
from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from .gateway import ModelGateway
from .nodes import STAGE_NODES

log = structlog.get_logger(__name__)


def build_graph(session: Session, user_id: str) -> Any:
    """Compile the agent stages into a linear graph.

    Session and user are bound at build time because a LangGraph node only
    receives state; the surrounding infrastructure is closed over instead.
    """
    builder = StateGraph(StoryState)

    def make_node(stage: StageName):  # type: ignore[no-untyped-def]
        node = STAGE_NODES[stage]

        def run(state: StoryState) -> dict[str, Any]:
            gateway = ModelGateway(
                session, stage=stage.value, version_id=state.version_id, user_id=user_id
            )
            updated = node(session, state, gateway)
            if stage not in updated.completed_stages:
                updated.completed_stages.append(stage)
            return updated.model_dump(mode="python")

        run.__name__ = stage.value
        return run

    previous: str = START
    for stage in AGENT_STAGES:
        builder.add_node(stage.value, make_node(stage))
        builder.add_edge(previous, stage.value)
        previous = stage.value

    builder.add_edge(previous, END)
    return builder.compile()


def run_agent_stages(session: Session, state: StoryState, user_id: str) -> StoryState:
    graph = build_graph(session, user_id)
    result = graph.invoke(state)
    return StoryState.model_validate(result)
