"""The video editor's wire format.

One manifest describes a cut: what to keep, how the captions look, what plays
underneath, and which frame it is delivered in. The web editor posts it, the
`video_edits` row stores it, and the render task reads it - so it belongs here
rather than in either service.

A cut is rendered from the same source artifacts the pipeline used (scene images
plus per-line audio), not by re-cutting `final_video`. Two reasons: that file has
captions burned into it by `compose_video`, so restyling them there is
impossible, and rebuilding the narration is what lets a listener drop the
generated score in favour of a track of their own.

Nothing here reaches an ffmpeg command line as text. Fonts, aspect ratios and
positions are enums the renderer looks up in its own tables, and free text
(captions, titles, watermark) is written to files ffmpeg reads as data. That is
the same rule the pipeline already follows for model output.
"""

import hashlib
import json
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .limits import (
    MAX_CAPTION_OVERRIDES,
    MAX_LINE_CHARS,
    MAX_WATERMARK_CHARS,
    TITLE_CARD_MAX_MS,
    TITLE_CARD_MIN_MS,
)
from .models import DialogueLine, LineId, SceneId, Strict

# Colours arrive from a browser colour input, which only ever emits this form.
# Anchored, fixed length, and hex-only: the renderer converts it to ASS's
# `&HBBGGRR&` itself, so a value that gets this far cannot carry filter syntax.
HEX_COLOR = r"^#[0-9A-Fa-f]{6}$"


class AspectRatio(StrEnum):
    """Delivery frame. Values are persisted in manifests, so treat them as a
    wire format."""

    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"
    PORTRAIT_4_5 = "4:5"


class CaptionFont(StrEnum):
    """Font *choices*, not font files.

    The renderer maps each of these to a family that the image installs, and
    falls back through fontconfig when a family is missing. Naming the choice
    rather than the file is what keeps a font name off the command line.
    """

    SANS = "sans"
    SERIF = "serif"
    MONO = "mono"
    NOTO_SANS = "noto_sans"
    NOTO_SERIF = "noto_serif"


class FrameFill(StrEnum):
    """What to do when the artwork is not the shape of the delivery frame.

    The pipeline's images are square, so exporting to 9:16 or 16:9 always has to
    lose something or invent something. `CROP` fills the frame and loses the
    edges; `BLUR` keeps the whole picture and fills the margins with a blurred
    copy of it, which is what makes a square illustration survive a vertical
    export with its composition intact.
    """

    CROP = "crop"
    BLUR = "blur"


class CaptionPosition(StrEnum):
    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class Corner(StrEnum):
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"


class RenderStatus(StrEnum):
    """Lifecycle of one cut.

    `DRAFT` is a manifest nobody has rendered yet. A cut that has been rendered
    and then edited goes back to `DRAFT`, which is what stops the editor from
    offering a stale MP4 as the current cut.
    """

    DRAFT = "draft"
    QUEUED = "queued"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"


class CaptionStyle(Strict):
    """How the burned-in captions look.

    Defaults are the readable-on-a-phone case rather than the subtle one: this
    footage is shared to feeds that autoplay muted, where a caption nobody can
    read is the same as no caption.
    """

    enabled: bool = True
    font: CaptionFont = CaptionFont.SANS
    size_pt: int = Field(default=30, ge=12, le=96)
    primary_color: str = Field(default="#FFFFFF", pattern=HEX_COLOR)
    outline_color: str = Field(default="#101010", pattern=HEX_COLOR)
    outline_px: int = Field(default=3, ge=0, le=12)
    shadow_px: int = Field(default=0, ge=0, le=8)
    # A backing box survives a bright frame that an outline alone does not.
    box: bool = True
    box_opacity: float = Field(default=0.55, ge=0.0, le=1.0)
    position: CaptionPosition = CaptionPosition.BOTTOM
    margin_px: int = Field(default=64, ge=0, le=400)
    bold: bool = True
    italic: bool = False
    uppercase: bool = False
    # Wrapping is done by us rather than by libass so the preview in the browser
    # and the rendered frame break lines in the same places.
    max_chars_per_line: int = Field(default=42, ge=16, le=80)
    show_speaker: bool = True


class TrimRange(Strict):
    """The span of the episode to keep, in milliseconds from the start.

    `end_ms` of None means "to the end", which is what a freshly created cut
    holds: the editor should not have to know the duration to open a draft.
    """

    start_ms: int = Field(default=0, ge=0)
    end_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "TrimRange":
        if self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("trim end_ms must be greater than start_ms")
        return self


class AudioMix(Strict):
    """What plays under the cut.

    The narration is rebuilt from the per-line clips, so the generated score is
    a layer that can be dropped rather than something already baked in. That is
    the whole reason a listener can put their own track underneath.
    """

    narration_gain_db: float = Field(default=0.0, ge=-24.0, le=12.0)
    # The bed `music_generation` produced. Off is how you make room for your own.
    keep_score: bool = True
    score_gain_db: float = Field(default=0.0, ge=-24.0, le=12.0)
    # A `local_audio` asset the user uploaded. None means narration only.
    local_asset_id: str | None = Field(default=None, max_length=64)
    local_gain_db: float = Field(default=-10.0, ge=-40.0, le=12.0)
    # Positive delays the track, negative starts it partway in. This is the
    # "sync" control: it is how a song's downbeat gets lined up with a cut.
    local_offset_ms: int = Field(default=0, ge=-600_000, le=600_000)
    local_loop: bool = True
    # Sidechain ducking, the same treatment `compose_episode` gives the score.
    duck_under_narration: bool = True
    fade_in_ms: int = Field(default=0, ge=0, le=10_000)
    fade_out_ms: int = Field(default=0, ge=0, le=10_000)


class TitleCard(Strict):
    """A held frame before the first scene. Off by default - it costs runtime at
    the start of a clip, which is the most expensive place to spend it."""

    enabled: bool = False
    heading: str = Field(default="", max_length=80)
    subheading: str = Field(default="", max_length=120)
    duration_ms: int = Field(
        default=2500, ge=TITLE_CARD_MIN_MS, le=TITLE_CARD_MAX_MS
    )


class Watermark(Strict):
    enabled: bool = False
    text: str = Field(default="", max_length=MAX_WATERMARK_CHARS)
    position: Corner = Corner.BOTTOM_RIGHT
    opacity: float = Field(default=0.7, ge=0.1, le=1.0)


class Motion(Strict):
    """Ken Burns and the crossfade between frames.

    Both are worth turning off: the motion fights fine detail in an illustration,
    and a hard cut reads as deliberate where a half-second dissolve reads as a
    slideshow.
    """

    ken_burns: bool = True
    transition_ms: int = Field(default=500, ge=0, le=2000)


class VideoEditManifest(Strict):
    """A complete description of one cut.

    Every field has a default, so `VideoEditManifest()` is the "same as the
    pipeline made it, in landscape" starting point a new draft opens on.
    """

    aspect: AspectRatio = AspectRatio.LANDSCAPE
    frame_fill: FrameFill = FrameFill.CROP
    trim: TrimRange = TrimRange()
    caption: CaptionStyle = CaptionStyle()
    # Replacement caption text, by line id. The script is model output and
    # sometimes reads badly on screen even when it sounds right; this fixes the
    # caption without re-running the pipeline or touching the audio.
    caption_overrides: dict[LineId, str] = Field(default_factory=dict)
    audio: AudioMix = AudioMix()
    title_card: TitleCard = TitleCard()
    watermark: Watermark = Watermark()
    motion: Motion = Motion()

    @field_validator("caption_overrides")
    @classmethod
    def _bounded_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_CAPTION_OVERRIDES:
            raise ValueError(f"at most {MAX_CAPTION_OVERRIDES} caption overrides")
        for line_id, text in value.items():
            # Line ids are minted by `ids.line_id`, so anything else is either a
            # stale manifest or someone probing. The renderer only ever looks
            # these up in a dict built from the story's own lines, but rejecting
            # them here keeps the stored manifest honest.
            if len(line_id) > 64:
                raise ValueError("caption override key is not a line id")
            if len(text) > MAX_LINE_CHARS:
                raise ValueError(f"caption override longer than {MAX_LINE_CHARS} characters")
        return value


# --- timeline --------------------------------------------------------------
#
# Where each line sits in the finished episode. The renderer needs it to cut the
# picture, the API needs it to tell the browser where the captions go, and both
# have to agree to the millisecond - so it is derived once, here, rather than
# implemented on each side of the wire.


class LineSpan(Strict):
    """One line's place in the untrimmed episode.

    `audio_ms` is the spoken clip; `pause_ms` is the silence `compose_episode`
    pads after it. The picture is held for both, and the caption is shown for the
    first only, so it clears during a beat rather than hanging over the silence.
    """

    line_id: LineId
    scene_id: SceneId
    index: int
    start_ms: int
    audio_ms: int
    pause_ms: int
    text: str
    speaker: str

    @property
    def frame_ms(self) -> int:
        return self.audio_ms + self.pause_ms

    @property
    def caption_end_ms(self) -> int:
        return self.start_ms + self.audio_ms

    @property
    def end_ms(self) -> int:
        return self.start_ms + self.frame_ms


def build_timeline(
    lines: list[DialogueLine], durations: dict[str, int]
) -> list[LineSpan]:
    """Absolute start times for every line, in episode order.

    Mirrors how `compose_episode` lays the mix out: each clip, then its trailing
    pause. `durations` maps a line id to the length of its `line_audio` asset, so
    a line whose synthesis failed contributes nothing and does not push the lines
    after it out of step with the audio that actually exists.
    """
    spans: list[LineSpan] = []
    cursor = 0
    for line in sorted(lines, key=lambda item: item.index):
        audio_ms = max(int(durations.get(line.id) or 0), 0)
        if audio_ms == 0:
            continue
        pause_ms = max(int(line.pause_after_ms or 0), 0)
        spans.append(
            LineSpan(
                line_id=line.id,
                scene_id=line.scene_id,
                index=line.index,
                start_ms=cursor,
                audio_ms=audio_ms,
                pause_ms=pause_ms,
                text=line.text,
                speaker=line.speaker or "",
            )
        )
        cursor += audio_ms + pause_ms
    return spans


def timeline_duration_ms(spans: list[LineSpan]) -> int:
    return spans[-1].end_ms if spans else 0


def manifest_digest(manifest: VideoEditManifest) -> str:
    """A short, stable digest of a manifest.

    Comparing this against the digest stored alongside a render is how both the
    API and the editor tell a current cut from a stale one, without diffing two
    JSON blobs. Sorted keys because a dict's order is not part of its meaning,
    and truncated because this identifies one of a handful of cuts on one story,
    not one of everything.
    """
    payload = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# --- presets ---------------------------------------------------------------
#
# One-click starting points. These are the product's opinion about what looks
# good, kept next to the manifest so the API can publish them and the editor
# does not hard-code a second copy.

CAPTION_PRESETS: dict[str, CaptionStyle] = {
    "clean": CaptionStyle(),
    "bold_social": CaptionStyle(
        font=CaptionFont.SANS,
        size_pt=44,
        outline_px=6,
        box=False,
        uppercase=True,
        position=CaptionPosition.MIDDLE,
        max_chars_per_line=24,
        show_speaker=False,
    ),
    "cinematic": CaptionStyle(
        font=CaptionFont.SERIF,
        size_pt=28,
        box=False,
        outline_px=2,
        shadow_px=3,
        bold=False,
        margin_px=90,
        show_speaker=False,
    ),
    "screenplay": CaptionStyle(
        font=CaptionFont.MONO,
        size_pt=24,
        box=True,
        box_opacity=0.7,
        bold=False,
        max_chars_per_line=52,
        show_speaker=True,
    ),
    "off": CaptionStyle(enabled=False),
}

# Pixel dimensions per frame, all even numbers because yuv420p needs them and
# libx264 refuses an odd one.
ASPECT_SIZES: dict[AspectRatio, tuple[int, int]] = {
    AspectRatio.LANDSCAPE: (1280, 720),
    AspectRatio.PORTRAIT: (720, 1280),
    AspectRatio.SQUARE: (1080, 1080),
    AspectRatio.PORTRAIT_4_5: (1080, 1350),
}
