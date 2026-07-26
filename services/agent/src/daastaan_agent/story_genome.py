"""Story Genome — structural DNA analysis of a finished story.

Computes quantitative metrics directly from `StoryState` (no LLM needed), then
makes one LLM call to synthesise those stats with the story content into trait
scores, arc classification, and new story concept suggestions.

This module sits beside the main pipeline and never enters `STAGE_NODES`.
"""

import structlog
from daastaan_contracts import StoryState
from daastaan_contracts.models import StoryGenomeResult

from .gateway import ModelGateway

log = structlog.get_logger(__name__)

_SYSTEM = """\
You are a narrative analyst who extracts the "DNA" of stories — their structural
traits, emotional signatures, and thematic fingerprints.

You will receive a story along with computed statistics (lines per scene,
dialogue ratio, character screen time, mood progression).  Use both the stats
and the actual story content to produce:

1. Story DNA traits: 4-6 scored dimensions (0.0-1.0) with brief explanations.
   Examples: "slow-burn tension", "ensemble cast", "twist-heavy", "lyrical prose",
   "high-action", "dialogue-driven", "atmospheric", "character-study".
   Choose traits that genuinely describe THIS story.

2. Arc shape classification: identify the narrative structure.
   Examples: "classic three-act", "in medias res", "circular", "episodic",
   "rising action only", "tragedy arc", "hero's journey".

3. Pacing profile: a short description of how the story moves.
   Examples: "slow build to explosive climax", "even and measured throughout",
   "front-loaded action with reflective ending".

4. 2-3 new story concept suggestions that would appeal to someone who loved
   this story.  Each concept should have a title, premise, and explanation of
   why it shares emotional DNA with the original."""


def _compute_stats(state: StoryState) -> dict:
    """Compute structural metrics from StoryState without any LLM calls."""
    lines = state.lines
    scenes = state.scenes

    # Lines per scene
    lines_per_scene: dict[str, int] = {}
    for scene in scenes:
        lines_per_scene[scene.title] = sum(
            1 for l in lines if l.scene_id == scene.id
        )

    # Dialogue-to-narration ratio
    dialogue_count = sum(1 for l in lines if l.line_type == "dialogue")
    narration_count = sum(1 for l in lines if l.line_type == "narration")
    total = dialogue_count + narration_count
    dialogue_ratio = dialogue_count / total if total > 0 else 0.0

    # Character screen time (% of lines per character)
    char_lines: dict[str, int] = {}
    for line in lines:
        speaker = line.speaker or "Unknown"
        char_lines[speaker] = char_lines.get(speaker, 0) + 1
    character_balance = {
        name: round(count / total, 3) if total > 0 else 0.0
        for name, count in char_lines.items()
    }

    # Mood tag sequence across scenes
    mood_sequence = [scene.mood_tag for scene in scenes]

    return {
        "lines_per_scene": lines_per_scene,
        "dialogue_ratio": round(dialogue_ratio, 3),
        "character_balance": character_balance,
        "mood_sequence": mood_sequence,
        "total_lines": total,
        "scene_count": len(scenes),
        "character_count": len(state.characters),
    }


def _genome_payload(state: StoryState, stats: dict) -> str:
    """Build payload combining story content and computed stats."""
    parts = []
    if state.title:
        parts.append(f"Title: {state.title}")
    if state.arc_summary:
        parts.append(f"Arc: {state.arc_summary}")
    if state.mood:
        parts.append(f"Genre: {state.mood.genre} | Mood: {state.mood.mood}")

    parts.append(f"\n## Computed Statistics")
    parts.append(f"Total lines: {stats['total_lines']}")
    parts.append(f"Scenes: {stats['scene_count']}")
    parts.append(f"Characters: {stats['character_count']}")
    parts.append(f"Dialogue ratio: {stats['dialogue_ratio']:.1%}")
    parts.append(f"Lines per scene: {stats['lines_per_scene']}")
    parts.append(f"Character balance: {stats['character_balance']}")
    parts.append(f"Mood progression: {' → '.join(stats['mood_sequence'])}")

    parts.append(f"\n## Story Content")
    for scene in state.scenes:
        parts.append(f"\n--- Scene {scene.index + 1}: {scene.title} ---")
        parts.append(f"Setting: {scene.setting} | Mood: {scene.mood_tag}")
        parts.append(scene.summary)
        scene_lines = [l for l in state.lines if l.scene_id == scene.id]
        for line in sorted(scene_lines, key=lambda l: l.index):
            tag = f"[{line.speaker}]" if line.line_type == "dialogue" else "[Narration]"
            parts.append(f"  {tag} {line.text}")

    full = "\n".join(parts)
    return full[:12_000]


def analyze_genome(state: StoryState, gateway: ModelGateway) -> StoryGenomeResult:
    """Compute stats locally, then one LLM call for synthesis."""
    stats = _compute_stats(state)
    payload = _genome_payload(state, stats)

    result = gateway.structured(
        schema=StoryGenomeResult,
        system=_SYSTEM,
        user_content=(
            f"Analyse the DNA of this story:\n\n"
            f"<story>\n{payload}\n</story>"
        ),
        kind="light",
        temperature=0.7,
    )

    # Override the LLM's dialogue_ratio and character_balance with our
    # precisely computed values
    result.dialogue_ratio = stats["dialogue_ratio"]
    result.character_balance = stats["character_balance"]

    return result
