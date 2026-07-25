"""Read-only continuity review for a finished story.

This module deliberately sits beside the pipeline nodes rather than in their
registry. A consistency review consumes the persisted story state, produces a
small structured editorial report, and never changes the story/version/media
state that it inspected.
"""

import json
from typing import TYPE_CHECKING

import structlog
from daastaan_contracts import (
    ConsistencyAnalysisOutput,
    ConsistencyFinding,
    StoryState,
)

from . import prompts

if TYPE_CHECKING:
    from .gateway import ModelGateway

log = structlog.get_logger(__name__)

# These are output-display limits, separate from the model schema. They make a
# malformed cache row or unexpectedly verbose response harmless to the API/UI.
MAX_FINDINGS = 12
MAX_SUMMARY_CHARS = 500
MAX_EXPLANATION_CHARS = 600
MAX_SUGGESTION_CHARS = 500


def _display_text(value: str, *, limit: int) -> str:
    """Normalise model prose before persisting it for a browser.

    React escapes text, but removing control characters and collapsing runs of
    whitespace here also means this data is safe for future non-React clients
    and cannot turn into an unbounded log/export field.
    """
    printable = "".join(char for char in value if char.isprintable() or char.isspace())
    return " ".join(printable.split())[:limit].rstrip()


def consistency_payload(state: StoryState) -> str:
    """Return the bounded generated material the checker is allowed to read.

    Notably absent: ``raw_text`` and any user-provided feedback. The continuity
    pass reasons over the exact scene/line artefacts that will be published.
    """
    payload = {
        "title": state.title,
        "arc_summary": state.arc_summary,
        "setting": state.setting,
        "characters": [
            {"name": character.name, "role": character.role.value}
            for character in state.characters
        ],
        "scenes": [
            {
                "scene_index": scene.index,
                "title": scene.title,
                "summary": scene.summary,
                "setting": scene.setting,
            }
            for scene in sorted(state.scenes, key=lambda scene: scene.index)
        ],
        "lines": [
            {
                "line_id": line.id,
                "scene_id": line.scene_id,
                "line_index": line.index,
                "speaker": line.speaker,
                "text": line.text,
            }
            for line in sorted(state.lines, key=lambda line: line.index)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def sanitise_analysis(
    state: StoryState, result: ConsistencyAnalysisOutput
) -> tuple[str, list[ConsistencyFinding]]:
    """Bind model output to known story ids and discard untrusted references."""
    scenes_by_index = {scene.index: scene for scene in state.scenes}
    known_lines = {line.id: line for line in state.lines}
    findings: list[ConsistencyFinding] = []

    for raw in result.findings[:MAX_FINDINGS]:
        scene = scenes_by_index.get(raw.scene_index)
        if scene is None:
            log.warning(
                "consistency_unknown_scene_reference",
                story_id=state.story_id,
                scene_index=raw.scene_index,
            )
            continue

        line_id = raw.line_id if raw.line_id in known_lines else None
        if raw.line_id and line_id is None:
            log.warning(
                "consistency_unknown_line_reference",
                story_id=state.story_id,
                line_id=raw.line_id,
            )

        explanation = _display_text(raw.explanation, limit=MAX_EXPLANATION_CHARS)
        suggestion = _display_text(raw.suggestion, limit=MAX_SUGGESTION_CHARS)
        # A vague category without a concrete explanation/suggestion is not a
        # useful report, and retaining it would expose unsupported model prose.
        if not explanation or not suggestion:
            continue

        findings.append(
            ConsistencyFinding(
                severity=raw.severity,
                type=raw.type,
                scene_id=scene.id,
                line_id=line_id,
                explanation=explanation,
                suggestion=suggestion,
            )
        )

    summary = _display_text(result.summary, limit=MAX_SUMMARY_CHARS)
    if not summary:
        summary = (
            "No material continuity issues found."
            if not findings
            else f"Found {len(findings)} continuity issue{'s' if len(findings) != 1 else ''}."
        )
    return summary, findings


def review_story_consistency(
    state: StoryState, gateway: "ModelGateway"
) -> tuple[str, list[ConsistencyFinding]]:
    """Ask the lightweight model for a bounded, verified editorial report."""
    result = gateway.structured(
        schema=ConsistencyAnalysisOutput,
        system=prompts.consistency_check_prompt(state.language),
        user_content=f"<published_story>\n{consistency_payload(state)}\n</published_story>",
        kind="light",
        temperature=0.1,
    )
    return sanitise_analysis(state, result)
