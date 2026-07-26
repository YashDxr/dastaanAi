"""Cliffhanger Optimizer — analyse and suggest improvements for story endings.

Extracts the final scene and its lines from `StoryState`, then makes one LLM
call to score the current ending, identify unresolved threads, and generate
alternative endings with "binge probability" scores.

This module sits beside the main pipeline and never enters `STAGE_NODES`.
"""

import structlog
from daastaan_contracts import StoryState
from daastaan_contracts.models import CliffhangerResult

from .gateway import ModelGateway

log = structlog.get_logger(__name__)

_SYSTEM = """\
You are a story ending analyst specialising in serialised fiction, podcasts,
and audiobooks.  Your job is to evaluate how well a story's ending creates
tension and anticipation for a potential sequel or next episode.

Given the full story with emphasis on the final scene, you must:

1. Score the current ending (1-10) for cliffhanger effectiveness
2. Analyse what makes the current ending work or not work
3. Rate the overall tension level (1-10)
4. List any unresolved narrative threads (plot points left open)
5. Suggest 2-3 alternative endings, each with:
   - A short title
   - A 2-3 sentence sketch of the alternative
   - A tension score (1-10)
   - A "binge probability" (1-100): how likely a listener is to immediately
     start the next episode/story based on this ending

Be specific about what makes each alternative compelling.  Endings should be
realistic variations that preserve the story's tone and characters."""


def _ending_payload(state: StoryState) -> str:
    """Build a payload focused on the ending, with enough context."""
    parts = []
    if state.title:
        parts.append(f"Title: {state.title}")
    if state.arc_summary:
        parts.append(f"Arc: {state.arc_summary}")
    if state.mood:
        parts.append(f"Genre: {state.mood.genre} | Mood: {state.mood.mood}")

    # Include all scenes for context, but emphasise the last one
    for i, scene in enumerate(state.scenes):
        is_last = i == len(state.scenes) - 1
        marker = " [FINAL SCENE — focus your analysis here]" if is_last else ""
        parts.append(f"\n--- Scene {scene.index + 1}: {scene.title}{marker} ---")
        parts.append(f"Setting: {scene.setting} | Mood: {scene.mood_tag}")
        parts.append(scene.summary)
        scene_lines = [l for l in state.lines if l.scene_id == scene.id]
        for line in sorted(scene_lines, key=lambda l: l.index):
            tag = f"[{line.speaker}]" if line.line_type == "dialogue" else "[Narration]"
            parts.append(f"  {tag} {line.text}")

    full = "\n".join(parts)
    return full[:12_000]


def analyze_cliffhanger(state: StoryState, gateway: ModelGateway) -> CliffhangerResult:
    """One LLM call to analyse the story's ending."""
    payload = _ending_payload(state)

    return gateway.structured(
        schema=CliffhangerResult,
        system=_SYSTEM,
        user_content=(
            f"Analyse the ending of this story:\n\n"
            f"<story>\n{payload}\n</story>"
        ),
        kind="light",
        temperature=0.7,
    )
