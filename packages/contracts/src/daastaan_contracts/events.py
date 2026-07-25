"""The progress event wire format.

Every event a worker pushes towards a listening browser is one of the models
below. They were previously ad-hoc dicts assembled at each call site, which made
two things impossible: the client could not tell which fields it was allowed to
read, and adding a field meant grepping for string literals to find who else
emitted the same `type`.

`type` is a discriminator, so one `validate_python` call over the union rejects a
malformed frame at the edge instead of letting it reach a client reducer as `Any`.

Ordering is *not* carried in the payload. Events are stored in a Redis stream and
each one is delivered with its stream ID as the SSE `id:`, so a reconnecting
client replays strictly after what it already has - exact by construction, with no
sequence number to reconcile. What the transport cannot promise is that two
workers racing on the same fan-out publish in the order they incremented the
counter, so counters like `StageProgressEvent.completed` are monotonic only when
the client folds them with `max`.

Unknown event types must be *ignored* by clients rather than treated as an error:
that is what lets a worker deployed ahead of a browser tab emit a new event kind
without breaking the tab.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from .enums import AssetKind, FeedbackStatus, JobStatus, Scope, StageName


class StageEvent(BaseModel):
    """A stage changed status. The `jobs` table holds the same transition."""

    type: Literal["stage"] = "stage"
    stage: StageName
    status: JobStatus
    error: str | None = None


class StageProgressEvent(BaseModel):
    """Sub-stage progress for work that is a group of subtasks.

    TTS and image generation fan out to one task per line and per scene. Without
    this the stage sits at `running` for the entire multi-minute fan-out, which is
    the largest single gap between what the pipeline is doing and what the user
    can see.

    `total` is how many subtasks this run actually enqueued, not how many lines the
    story has: a regeneration that respeaks one line reports 1, not 40. `completed`
    is read from a Redis counter, so it is accurate even though the publishers are
    concurrent - but see the module docstring on ordering.
    """

    type: Literal["stage_progress"] = "stage_progress"
    stage: StageName
    completed: int = Field(ge=0)
    total: int = Field(ge=0)


class StagePreviewEvent(BaseModel):
    """Content the model has produced so far, mid-call.

    Emitted from the streaming structured-output parse, so the browser shows
    characters and scene titles appearing while the stage is still running instead
    of waiting for it to commit. `items` are display-ready strings and each is sent
    at most once per stage: the client appends, it does not replace.
    """

    type: Literal["stage_preview"] = "stage_preview"
    stage: StageName
    label: str
    items: list[str] = Field(default_factory=list)


class StageTokensEvent(BaseModel):
    """Output-token count for a stage still in flight.

    A stage can run a long time with nothing display-worthy to show - a long arc
    summary being drafted, for instance - and a UI with no signal during that
    window looks stalled. Deliberately just a counter: the cheapest honest proof
    that the model is still producing.
    """

    type: Literal["stage_tokens"] = "stage_tokens"
    stage: StageName
    tokens: int = Field(ge=0)


class AssetEvent(BaseModel):
    """One media artifact finished and is now fetchable."""

    type: Literal["asset"] = "asset"
    kind: AssetKind
    line_id: str | None = None
    scene_id: str | None = None
    duration_ms: int | None = None


class MusicStatusEvent(BaseModel):
    """The score degraded without the episode failing."""

    type: Literal["music_status"] = "music_status"
    status: Literal["unavailable"]
    error: str


class FeedbackEvent(BaseModel):
    type: Literal["feedback"] = "feedback"
    status: FeedbackStatus
    feedback_id: str
    error: str | None = None
    version_id: str | None = None
    scope: Scope | None = None
    target_stage: StageName | None = None
    target_id: str | None = None


class CompleteEvent(BaseModel):
    """The episode is playable. Terminal for a run."""

    type: Literal["complete"] = "complete"
    version_id: str


class HeartbeatEvent(BaseModel):
    """Proof of liveness during a quiet stretch.

    SSE sends a comment line instead, which `EventSource` never surfaces; this
    exists for the WebSocket path, where there is no comment syntax and a client
    otherwise cannot tell idle from dead.
    """

    type: Literal["heartbeat"] = "heartbeat"


type ProgressEvent = Annotated[
    StageEvent
    | StageProgressEvent
    | StagePreviewEvent
    | StageTokensEvent
    | AssetEvent
    | MusicStatusEvent
    | FeedbackEvent
    | CompleteEvent
    | HeartbeatEvent,
    Field(discriminator="type"),
]

# Built once at import rather than per call: this validates on the hot path of
# every published frame.
progress_event_adapter: TypeAdapter[ProgressEvent] = TypeAdapter(ProgressEvent)
