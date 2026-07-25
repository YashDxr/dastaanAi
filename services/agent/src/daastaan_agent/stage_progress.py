"""Turning model stream deltas into published progress events.

This sits between two things that deliberately do not know about each other: the
gateway, which owns paid calls and reports raw deltas without any idea what a story
is, and the event bus, which carries display state. Keeping the join here is what
lets `gateway.py` stay a billing and prompt-safety boundary rather than also
becoming a progress publisher.

The reporter is built per stage execution and closes over what has already been
sent, because the preview contract is append-only: a client is told about each
character or scene title once and keeps them, so it never has to diff a list it is
already rendering.
"""

from collections.abc import Callable

import structlog
from daastaan_contracts import StageName, StagePreviewEvent, StageTokensEvent

from . import repo
from .gateway import StreamedDelta
from .previews import preview_items, preview_label

log = structlog.get_logger(__name__)


def stage_reporter(story_id: str, stage: StageName) -> Callable[[StreamedDelta], None]:
    """Build the `on_delta` callback for one stage execution.

    A stage reports either content or liveness, never both. Stages with a projection
    publish the items they produce; the rest publish a token count, which is the only
    honest signal available when there is nothing recognisable to show. Emitting both
    would put frames on the bus that no client renders - the studio hides the counter
    whenever a preview exists - and a stage with a projection would spend most of its
    publishes on a number nobody reads.
    """
    label = preview_label(stage)
    if label is None:
        return _token_reporter(story_id, stage)
    return _preview_reporter(story_id, stage, label)


def _preview_reporter(
    story_id: str, stage: StageName, label: str
) -> Callable[[StreamedDelta], None]:
    sent = 0

    def report(delta: StreamedDelta) -> None:
        nonlocal sent
        items = preview_items(stage, delta.parsed)
        # Only the tail is published. The client appends, so resending items it is
        # already showing would make every list flicker as the stage progressed.
        if len(items) <= sent:
            return
        repo.publish(story_id, StagePreviewEvent(stage=stage, label=label, items=items[sent:]))
        sent = len(items)

    return report


def _token_reporter(story_id: str, stage: StageName) -> Callable[[StreamedDelta], None]:
    reported = 0

    def report(delta: StreamedDelta) -> None:
        nonlocal reported
        # Monotonic guard: the estimate is derived from the response text, which only
        # grows, but a retried attempt restarts it from zero and a counter that jumps
        # backwards looks like a stall.
        if delta.tokens <= reported:
            return
        reported = delta.tokens
        repo.publish(story_id, StageTokensEvent(stage=stage, tokens=delta.tokens))

    return report
