"""AI Writers Room — multi-persona critique of a finished story.

Four virtual personas (Director, Editor, Psychologist, Audience Rep) each
independently review the story, then a final synthesis pass merges their
critiques into a revision brief.  Five LLM calls total: four fan-out,
one fan-in.

This module sits beside the main pipeline and never enters `STAGE_NODES`.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import structlog
from daastaan_contracts import StoryState
from daastaan_contracts.models import (
    PersonaCritique,
    RevisionBrief,
    WritersRoomResult,
)

from .gateway import ModelGateway

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Persona system prompts
# ---------------------------------------------------------------------------

_DIRECTOR = """\
You are an experienced film/audiobook Director reviewing a story for pacing,
dramatic structure, and scene transitions.  Focus on:
- Whether the opening hooks the audience
- Pacing across scenes (too fast, too slow, well-balanced)
- Scene transition quality (abrupt, smooth, thematic)
- Dramatic arc effectiveness (setup, rising action, climax, resolution)
Give concrete, actionable observations.  Be specific about which scenes or
moments you are referring to."""

_EDITOR = """\
You are a senior literary Editor reviewing a story for clarity, dialogue
quality, and redundancy.  Focus on:
- Prose clarity and readability
- Dialogue naturalness and distinctiveness per character
- Redundant or repetitive passages
- Narrative consistency (facts, character details)
Give concrete, actionable observations with specific examples."""

_PSYCHOLOGIST = """\
You are a Character Psychologist reviewing a story for emotional depth,
character motivation, and relatability.  Focus on:
- Whether character motivations are clear and believable
- Emotional arc coherence (do reactions match events?)
- Character growth or lack thereof
- Relatability and audience connection with characters
Give concrete, actionable observations about specific characters."""

_AUDIENCE_REP = """\
You are an Audience Representative reviewing a story from a listener/reader
engagement perspective.  Focus on:
- Moments of confusion or disengagement
- Emotional impact (did it land?)
- Surprise vs. predictability balance
- Overall satisfaction and "would I recommend this?"
Give concrete, actionable observations about the audience experience."""

_PERSONAS: list[tuple[str, str]] = [
    ("Director", _DIRECTOR),
    ("Editor", _EDITOR),
    ("Psychologist", _PSYCHOLOGIST),
    ("Audience Rep", _AUDIENCE_REP),
]

_SYNTHESIS = """\
You are a head writer synthesising four independent critiques of the same story
into a concise revision brief.  You will receive critiques from a Director,
Editor, Psychologist, and Audience Representative.

Produce:
- A 2-3 sentence summary of the overall consensus
- Key recurring themes across critiques
- A prioritised list of the most important actions the author should consider
- An overall score from 1 to 10 (10 = publication-ready, 1 = needs fundamental rework)"""


# ---------------------------------------------------------------------------
# Story payload helper
# ---------------------------------------------------------------------------

def _story_payload(state: StoryState) -> str:
    """Build a bounded text representation of the story for LLM consumption."""
    parts = []
    if state.title:
        parts.append(f"Title: {state.title}")
    if state.arc_summary:
        parts.append(f"Arc: {state.arc_summary}")
    if state.mood:
        parts.append(f"Genre: {state.mood.genre} | Mood: {state.mood.mood}")

    for scene in state.scenes:
        parts.append(f"\n--- Scene {scene.index + 1}: {scene.title} ---")
        parts.append(f"Setting: {scene.setting} | Mood: {scene.mood_tag}")
        parts.append(scene.summary)
        scene_lines = [l for l in state.lines if l.scene_id == scene.id]
        for line in sorted(scene_lines, key=lambda l: l.index):
            tag = f"[{line.speaker}]" if line.line_type == "dialogue" else "[Narration]"
            parts.append(f"  {tag} {line.text}")

    # Cap at ~12k chars to stay within context limits
    full = "\n".join(parts)
    return full[:12_000]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_writers_room(state: StoryState, gateway: ModelGateway) -> WritersRoomResult:
    """Fan-out 4 persona critiques in parallel, fan-in with a synthesis call."""
    payload = _story_payload(state)

    # Fan-out: 4 parallel LLM calls
    def _critique(persona_name: str, system: str) -> PersonaCritique:
        return gateway.structured(
            schema=PersonaCritique,
            system=system,
            user_content=(
                f"Review this story as the {persona_name}.\n\n"
                f"<story>\n{payload}\n</story>"
            ),
            kind="light",
            temperature=0.7,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(_critique, name, prompt)
            for name, prompt in _PERSONAS
        ]
        critiques = [f.result() for f in futures]

    # Fan-in: 1 synthesis call
    critique_text = "\n\n".join(
        f"## {c.persona}\n"
        f"Strengths: {', '.join(c.strengths)}\n"
        f"Concerns: {', '.join(c.concerns)}\n"
        f"Suggestions: {', '.join(c.suggestions)}"
        for c in critiques
    )

    brief = gateway.structured(
        schema=RevisionBrief,
        system=_SYNTHESIS,
        user_content=(
            f"Here are four independent critiques of the same story:\n\n"
            f"{critique_text}\n\n"
            f"Synthesise these into a revision brief."
        ),
        kind="light",
        temperature=0.4,
    )

    return WritersRoomResult(critiques=critiques, brief=brief)
