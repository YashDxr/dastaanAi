"""Turning a half-generated stage output into something worth showing a user.

A reasoning stage takes tens of seconds and, until now, showed nothing while it
ran: the stage chip said "running" and the story appeared all at once at the end.
The structured-output stream makes the intermediate object available, but that
object is a partially-parsed JSON dict and not something to put on screen - so
each stage gets a small projection here that answers "what has the model decided
so far, in words a listener would recognise".

Three constraints shape these:

* they run against *partial* data. A half-written character has a name and nothing
  else, and the last element of a streamed list is routinely a fragment. Anything
  without the field that identifies it is skipped rather than rendered blank;
* they must be stable as more tokens arrive. `preview_items` returns whole items in
  order and the caller only publishes ones it has not sent, so the growing list is
  append-only and a client never has to reconcile a replaced entry;
* they are cosmetic. Nothing here feeds state, cost, or control flow, so a stage
  with no useful projection simply returns nothing and loses only its preview.
"""

from collections.abc import Iterator
from typing import Any

from daastaan_contracts import StageName

# Long enough to recognise a line, short enough that a preview never turns into a
# second copy of the script on the wire.
_MAX_ITEM_CHARS = 90


def _text(value: Any) -> str | None:
    """A trimmed non-empty string, or `None` for anything else.

    Partial JSON yields half-written values and nulls, and every projection here
    needs the same "is this worth showing yet" answer.
    """
    if not isinstance(value, str):
        return None
    flat = " ".join(value.split())
    if not flat:
        return None
    return flat[:_MAX_ITEM_CHARS] + ("…" if len(flat) > _MAX_ITEM_CHARS else "")


def _entries(parsed: Any, field: str) -> list[dict[str, Any]]:
    """The list at `parsed[field]`, keeping only the dict elements.

    Partial parsing can leave a trailing element as a bare string or a number
    while its object literal is still being written.
    """
    if not isinstance(parsed, dict):
        return []
    value = parsed.get(field)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _scene_titles(parsed: Any) -> Iterator[str]:
    for scene in _entries(parsed, "scenes"):
        title = _text(scene.get("title"))
        if title:
            yield title


def _character_names(parsed: Any) -> Iterator[str]:
    for character in _entries(parsed, "characters"):
        name = _text(character.get("name"))
        if not name:
            continue
        role = _text(character.get("role"))
        # The role arrives a token or two after the name. Showing it when present
        # is what makes the list read as a cast rather than a word cloud.
        yield f"{name} — {role}" if role else name


def _script_lines(parsed: Any) -> Iterator[str]:
    for line in _entries(parsed, "lines"):
        text = _text(line.get("text"))
        if not text:
            continue
        speaker = _text(line.get("speaker"))
        yield f"{speaker}: {text}" if speaker else text


def _story_shape(parsed: Any) -> Iterator[str]:
    """Title and arc before the scene list, which is the order they are written in."""
    if not isinstance(parsed, dict):
        return
    title = _text(parsed.get("title"))
    if title:
        yield title
    arc = _text(parsed.get("arc_summary"))
    if arc:
        yield arc
    yield from _scene_titles(parsed)


def _narrator(parsed: Any) -> Iterator[str]:
    if not isinstance(parsed, dict):
        return
    name = _text(parsed.get("persona_name"))
    if name:
        yield name
    tone = _text(parsed.get("tone"))
    if tone:
        yield tone


def _mood(parsed: Any) -> Iterator[str]:
    if not isinstance(parsed, dict):
        return
    genre = _text(parsed.get("genre"))
    mood = _text(parsed.get("mood"))
    if genre and mood:
        yield f"{genre}, {mood}"
    elif genre or mood:
        yield genre or mood or ""


# What the client heads the preview list with, per stage. Only stages whose output
# has something a listener would recognise appear here: emotion tagging produces
# per-line instruction strings that mean nothing without the line beside them, and
# voice assignment runs no model at all.
_PROJECTIONS: dict[StageName, tuple[str, Any]] = {
    StageName.MOOD_CLASSIFICATION: ("Reading the mood", _mood),
    StageName.STORY_UNDERSTANDING: ("Shaping the story", _story_shape),
    StageName.CHARACTER_REGISTRY: ("Casting characters", _character_names),
    StageName.DIALOGUE_ATTRIBUTION: ("Writing the script", _script_lines),
    StageName.NARRATOR_PERSONA: ("Finding the narrator", _narrator),
}


def preview_label(stage: StageName) -> str | None:
    projection = _PROJECTIONS.get(stage)
    return projection[0] if projection else None


def preview_items(stage: StageName, parsed: Any) -> list[str]:
    """Display-ready items for a stage's partially generated output.

    Order is the order the model wrote them, and repeated calls on a growing
    `parsed` return a growing prefix of the same list. Empty for a stage with no
    projection, which the caller treats as "nothing to publish".
    """
    projection = _PROJECTIONS.get(stage)
    if projection is None:
        return []
    try:
        return list(projection[1](parsed))
    except Exception:
        # A projection reads loosely-typed partial JSON. Losing a preview frame is
        # not worth risking the stage that was generating it.
        return []
