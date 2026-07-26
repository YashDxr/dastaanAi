"""Rendering an edited cut with ffmpeg.

A cut is rebuilt from the artifacts the pipeline already produced - the per-line
images, the per-line audio, the generated score - rather than re-cut from
`final_video`. That file has captions burned into it by `compose_video`, so
restyling them there is impossible, and its narration and score are already mixed
together, so neither can be adjusted. Starting from the source assets costs an
encode and buys every control the editor offers.

Captions are rendered through libass rather than `drawtext`. Debian's ffmpeg is
built with it, the image already installs DejaVu, Noto and the Indic families, and
one subtitle file replaces one `drawtext` filter per line - which for a 60-line
script is the difference between a readable filter graph and an unreadable one.
It is also the only way to get a real font choice, timing per caption, and text
that is data in a file rather than an argument on a command line.

Same safety rules as `assembly`: argv lists, never `shell=True`, every path a
temp file this module created, and no user or model text anywhere except inside
files ffmpeg reads as data.
"""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog
from daastaan_contracts import (
    ASPECT_SIZES,
    CaptionFont,
    CaptionPosition,
    Corner,
    FrameFill,
    LineSpan,
    VideoEditManifest,
    timeline_duration_ms,
)

from .assembly import ffmpeg_path

log = structlog.get_logger(__name__)

FPS = 25
# Progressive, widely decodable, and small enough that a phone can stream it.
VIDEO_CODEC = ("-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p")
AUDIO_CODEC = ("-c:a", "aac", "-b:a", "192k")
# `faststart` moves the index to the front so the file plays before it has fully
# downloaded. A shared link is watched in a browser, so this is not optional.
MOVFLAGS = ("-movflags", "+faststart")

LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"

# Sigma for the blurred backdrop behind a letterboxed frame. High enough that the
# backdrop reads as texture rather than as a second, softer copy of the picture.
BLUR_SIGMA = 24

# Title card background. Matches the app's ink so a card does not flash white.
TITLE_CARD_BG = "0x121816"

# Ceiling on one render. Twice the pipeline's own video budget, because a cut can
# add a blurred backdrop and a subtitle pass on top of the same zoompan work.
RENDER_TIMEOUT_SECONDS = 1800

# How far a Ken Burns move travels. Deliberately small: the source is a still
# illustration, and anything more than this reads as a zoom rather than as drift.
_ZOOM_MAX = 1.12

# Fontconfig families, by the choice the editor offers. Every one of these is
# installed by `infra/docker/python.Dockerfile`; `_resolve_family` still checks,
# because a developer running the worker outside Docker may not have them.
_FONT_FAMILIES: dict[CaptionFont, str] = {
    CaptionFont.SANS: "DejaVu Sans",
    CaptionFont.SERIF: "DejaVu Serif",
    CaptionFont.MONO: "DejaVu Sans Mono",
    CaptionFont.NOTO_SANS: "Noto Sans",
    CaptionFont.NOTO_SERIF: "Noto Serif",
}

# ASS numpad alignment: 8 is top-centre, 5 middle-centre, 2 bottom-centre.
_ASS_ALIGNMENT: dict[CaptionPosition, int] = {
    CaptionPosition.TOP: 8,
    CaptionPosition.MIDDLE: 5,
    CaptionPosition.BOTTOM: 2,
}

_WATERMARK_ALIGNMENT: dict[Corner, int] = {
    Corner.TOP_LEFT: 7,
    Corner.TOP_RIGHT: 9,
    Corner.BOTTOM_LEFT: 1,
    Corner.BOTTOM_RIGHT: 3,
}


class VideoEditError(RuntimeError):
    pass


# --- segments --------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """One held picture in the cut, already clipped to the trim window.

    `caption_ms` is how long the caption shows, measured from the start of the
    segment. Zero means this segment carries no caption - a title card, or a line
    whose spoken part fell outside the trim.
    """

    image: bytes | None
    duration_ms: int
    caption: str
    caption_ms: int


def plan_segments(
    spans: list[LineSpan],
    images: dict[str, bytes],
    manifest: VideoEditManifest,
) -> list[Segment]:
    """Turn the episode timeline into the segments this cut is made of.

    Trimming happens here rather than in ffmpeg. A trim that lands mid-line has
    to shorten that line's picture *and* the caption over it, which a single
    `atrim` on the finished mix cannot express.
    """
    start_ms = manifest.trim.start_ms
    end_ms = (
        manifest.trim.end_ms
        if manifest.trim.end_ms is not None
        else timeline_duration_ms(spans)
    )
    if end_ms <= start_ms:
        raise VideoEditError("the trim window is empty")

    segments: list[Segment] = []

    if manifest.title_card.enabled:
        segments.append(
            Segment(
                image=None,
                duration_ms=manifest.title_card.duration_ms,
                caption="",
                caption_ms=0,
            )
        )

    for span in spans:
        if span.end_ms <= start_ms or span.start_ms >= end_ms:
            continue
        image = images.get(span.line_id) or images.get(span.scene_id)
        if image is None:
            log.warning("edit_segment_without_image", line_id=span.line_id)
            continue

        visible_from = max(span.start_ms, start_ms)
        visible_to = min(span.end_ms, end_ms)
        duration_ms = visible_to - visible_from
        if duration_ms <= 0:
            continue

        # The caption follows the spoken part, which may itself be clipped.
        spoken_to = min(span.start_ms + span.audio_ms, end_ms)
        caption_ms = max(spoken_to - visible_from, 0)
        segments.append(
            Segment(
                image=image,
                duration_ms=duration_ms,
                caption=_caption_text(span, manifest),
                caption_ms=caption_ms,
            )
        )

    if not any(segment.image is not None for segment in segments):
        raise VideoEditError("this trim keeps no scene artwork")
    return segments


def _caption_text(span: LineSpan, manifest: VideoEditManifest) -> str:
    if not manifest.caption.enabled:
        return ""
    text = manifest.caption_overrides.get(span.line_id, span.text).strip()
    if not text:
        return ""
    if manifest.caption.uppercase:
        text = text.upper()
    if manifest.caption.show_speaker and span.speaker:
        speaker = span.speaker.upper() if manifest.caption.uppercase else span.speaker
        text = f"{speaker}: {text}"
    return wrap_caption(text, manifest.caption.max_chars_per_line)


def wrap_caption(text: str, width: int) -> str:
    """Break at word boundaries, newline-separated.

    Wrapping is done here rather than left to libass so the browser preview and
    the rendered frame break in the same places - a caption that reflows between
    the two makes the preview a suggestion rather than a proof.
    """
    lines: list[str] = []
    current: list[str] = []
    length = 0
    for word in text.split():
        needed = len(word) if not current else len(word) + 1
        if current and length + needed > width:
            lines.append(" ".join(current))
            current, length = [word], len(word)
        else:
            current.append(word)
            length += needed
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


# --- subtitles -------------------------------------------------------------


@lru_cache(maxsize=16)
def _resolve_family(family: str) -> str:
    """The installed family closest to `family`.

    libass resolves names through fontconfig, so an absent family silently
    becomes whatever fontconfig prefers. Asking first means the log says which
    font was actually used instead of leaving it to be discovered in the output.

    The requested name is returned either way: libass has to do its own lookup,
    and handing it fontconfig's answer would only introduce a second chance to
    get it wrong. This is diagnostics, not resolution.
    """
    binary = shutil.which("fc-match")
    if binary is None:
        return family
    try:
        result = subprocess.run(  # noqa: S603
            [binary, "-f", "%{family}", family],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        log.warning("font_match_failed", family=family, exc_info=True)
        return family
    matched = result.stdout.strip() if result.returncode == 0 else ""
    if matched and matched.split(",")[0].casefold() != family.casefold():
        log.info("font_substituted", requested=family, resolved=matched)
    return family


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    """`#RRGGBB` to ASS `&HAABBGGRR`.

    ASS orders the channels backwards from CSS and treats alpha as transparency,
    so 0 is opaque. The pattern on `CaptionStyle` guarantees the input shape.
    """
    red, green, blue = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def _ass_time(ms: int) -> str:
    ms = max(ms, 0)
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


def _ass_escape(text: str) -> str:
    """Make arbitrary text safe as an ASS dialogue payload.

    Braces open and close override blocks, so a caption containing one could
    otherwise reposition itself or change its own colour. They are replaced
    rather than escaped because ASS has no reliable escape for them. Newlines
    become the hard break `\\N`, and any other control character is dropped.
    """
    cleaned = "".join(char for char in text if char == "\n" or ord(char) >= 32)
    return (
        cleaned.replace("{", "(")
        .replace("}", ")")
        .replace("\n", "\\N")
    )


def build_ass(
    segments: list[Segment],
    manifest: VideoEditManifest,
    *,
    width: int,
    height: int,
    total_ms: int,
) -> str:
    """The subtitle script for a cut: captions, title card text and watermark.

    All three are text over video, so all three go through one file. A separate
    `drawtext` filter for the title and another for the watermark would be two
    more places for a quoting mistake to live.

    `PlayResX/Y` are set to the output size, which is what makes `size_pt` mean
    pixels at that size rather than a fraction of some other reference frame.
    """
    style = manifest.caption
    family = _resolve_family(_FONT_FAMILIES[style.font])
    # BorderStyle 3 paints an opaque box behind the text; 1 is outline plus drop
    # shadow. `box_opacity` only has somewhere to go in the first.
    border_style = 3 if style.box else 1
    back_alpha = round((1.0 - style.box_opacity) * 255) if style.box else 255

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 2",  # No automatic wrapping: `wrap_caption` already did it.
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
        " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,"
        " ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line(
            "Caption",
            family=family,
            size=style.size_pt,
            primary=_ass_color(style.primary_color),
            outline=_ass_color(style.outline_color),
            back=_ass_color(style.outline_color, back_alpha),
            bold=style.bold,
            italic=style.italic,
            border_style=border_style,
            outline_px=style.outline_px,
            shadow_px=style.shadow_px,
            alignment=_ASS_ALIGNMENT[style.position],
            margin_v=style.margin_px,
        ),
        _style_line(
            "Heading",
            family=family,
            size=max(round(height * 0.075), 28),
            primary="&H00FFFFFF",
            outline="&H00101816",
            back="&HFF101816",
            bold=True,
            italic=False,
            border_style=1,
            outline_px=0,
            shadow_px=0,
            alignment=5,
            margin_v=round(height * 0.06),
        ),
        _style_line(
            "Subheading",
            family=family,
            size=max(round(height * 0.035), 18),
            primary="&H00C5D0CB",
            outline="&H00101816",
            back="&HFF101816",
            bold=False,
            italic=False,
            border_style=1,
            outline_px=0,
            shadow_px=0,
            alignment=2,
            margin_v=round(height * 0.36),
        ),
        _style_line(
            "Watermark",
            family=family,
            size=max(round(height * 0.028), 14),
            primary=_ass_color("#FFFFFF", round((1.0 - manifest.watermark.opacity) * 255)),
            outline=_ass_color("#101816", round((1.0 - manifest.watermark.opacity) * 255)),
            back="&HFF101816",
            bold=False,
            italic=False,
            border_style=1,
            outline_px=2,
            shadow_px=0,
            alignment=_WATERMARK_ALIGNMENT[manifest.watermark.position],
            margin_v=round(height * 0.03),
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events: list[str] = []
    cursor_ms = 0
    for segment in segments:
        if segment.caption and segment.caption_ms > 0:
            events.append(
                _dialogue(
                    "Caption",
                    cursor_ms,
                    cursor_ms + segment.caption_ms,
                    segment.caption,
                )
            )
        cursor_ms += segment.duration_ms

    card = manifest.title_card
    if card.enabled:
        if card.heading.strip():
            events.append(_dialogue("Heading", 0, card.duration_ms, card.heading.strip()))
        if card.subheading.strip():
            events.append(_dialogue("Subheading", 0, card.duration_ms, card.subheading.strip()))

    mark = manifest.watermark
    if mark.enabled and mark.text.strip():
        # Held for the whole cut, including over the title card: a shared clip is
        # most often watched from the first frame.
        events.append(_dialogue("Watermark", 0, max(total_ms, 1), mark.text.strip()))

    return "\n".join([*header, *events, ""])


def _style_line(
    name: str,
    *,
    family: str,
    size: int,
    primary: str,
    outline: str,
    back: str,
    bold: bool,
    italic: bool,
    border_style: int,
    outline_px: int,
    shadow_px: int,
    alignment: int,
    margin_v: int,
) -> str:
    return (
        f"Style: {name},{family},{size},{primary},{primary},{outline},{back},"
        f"{-1 if bold else 0},{-1 if italic else 0},0,0,"
        f"100,100,0,0,{border_style},{outline_px},{shadow_px},"
        f"{alignment},40,40,{margin_v},1"
    )


def _dialogue(style: str, start_ms: int, end_ms: int, text: str) -> str:
    return (
        f"Dialogue: 0,{_ass_time(start_ms)},{_ass_time(end_ms)},{style},,"
        f"0,0,0,,{_ass_escape(text)}"
    )


def _escape_filter_path(path: str) -> str:
    """Escape a path for use as a filter *option value*.

    These characters separate options and filters inside `filter_complex`, so an
    unescaped one truncates the option. Paths here are temp files this module
    created, so this is belt-and-braces rather than the only line of defence.
    """
    for char in ("\\", "'", ":", ";", "[", "]", ","):
        path = path.replace(char, f"\\{char}")
    return path


# --- video graph -----------------------------------------------------------


def _segment_video_filters(
    segments: list[Segment],
    manifest: VideoEditManifest,
    *,
    width: int,
    height: int,
) -> tuple[list[str], list[str]]:
    """Per-segment video chains, and the label each one ends on.

    Every chain lands on exactly `width x height`, square pixels, `yuv420p`, at
    `FPS`, because `xfade` refuses to join two streams that differ in any of
    those - which is the usual reason a graph like this fails to build.

    A still is one input frame, not a looped stream: `zoompan` emits `d` frames
    for every frame it is given, so an input that never ends would produce a
    segment that never ends. It is also why the no-motion case still goes through
    `zoompan`, at a fixed zoom of 1 - holding a frame for a known number of
    frames is exactly what that filter does.
    """
    filters: list[str] = []
    labels: list[str] = []
    transition_s = manifest.motion.transition_ms / 1000
    count = len(segments)

    for index, segment in enumerate(segments):
        duration_s = max(segment.duration_ms, 1) / 1000
        # xfade consumes material from both sides of the join, so every segment
        # but the last carries the overlap it is about to give away.
        if count > 1 and index < count - 1:
            duration_s += transition_s

        source = f"{index}:v"
        if segment.image is None:
            # A lavfi colour source is already the right size and frame rate.
            filters.append(f"[{source}]setsar=1,format=yuv420p[seg{index}]")
            labels.append(f"seg{index}")
            continue

        filters.append(_fit_filter(source, manifest, width=width, height=height, index=index))

        total_frames = max(round(duration_s * FPS), 1)
        zoom, x_expr, y_expr = (
            _ken_burns(index) if manifest.motion.ken_burns else ("1", "0", "0")
        )
        filters.append(
            f"[fit{index}]zoompan=z='{zoom}':x='{x_expr}':y='{y_expr}'"
            f":d={total_frames}:s={width}x{height}:fps={FPS},setsar=1,format=yuv420p[seg{index}]"
        )
        labels.append(f"seg{index}")

    return filters, labels


def _fit_filter(
    source: str,
    manifest: VideoEditManifest,
    *,
    width: int,
    height: int,
    index: int,
) -> str:
    """Bring one still to the delivery frame.

    `crop` fills the frame and loses whatever falls outside it. `blur` keeps the
    whole picture and fills the margins with a blurred copy of itself, which is
    what makes a square illustration survive a 9:16 export instead of losing its
    sides.
    """
    if manifest.frame_fill is FrameFill.BLUR:
        return (
            f"[{source}]split=2[bg{index}][fg{index}];"
            f"[bg{index}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma={BLUR_SIGMA}[bgb{index}];"
            f"[fg{index}]scale={width}:{height}:force_original_aspect_ratio=decrease[fgs{index}];"
            f"[bgb{index}][fgs{index}]overlay=(W-w)/2:(H-h)/2[fit{index}]"
        )
    return (
        f"[{source}]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}[fit{index}]"
    )


def _ken_burns(index: int) -> tuple[str, str, str]:
    """A zoom or pan expression, cycled so consecutive frames do not match.

    Four moves rather than one: an episode of identical slow zooms is more
    obviously mechanical than no motion at all.
    """
    moves = (
        (f"min(zoom+0.0004,{_ZOOM_MAX})", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        (f"if(eq(on,1),{_ZOOM_MAX},max(zoom-0.0004,1.0))", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        ("1.08", "if(eq(on,1),(iw-iw/zoom),max(x-0.6,0))", "ih/2-(ih/zoom/2)"),
        ("1.08", "if(eq(on,1),0,min(x+0.6,iw-iw/zoom))", "ih/2-(ih/zoom/2)"),
    )
    return moves[index % len(moves)]


def _xfade_chain(
    segments: list[Segment], labels: list[str], manifest: VideoEditManifest
) -> tuple[list[str], str]:
    transition_s = manifest.motion.transition_ms / 1000
    if len(labels) == 1 or transition_s <= 0:
        if len(labels) == 1:
            return [], labels[0]
        # A hard cut still needs the segments joined into one stream.
        joined = "".join(f"[{label}]" for label in labels)
        return [f"{joined}concat=n={len(labels)}:v=1:a=0[vjoined]"], "vjoined"

    filters: list[str] = []
    cumulative_s = 0.0
    previous = labels[0]
    for index in range(1, len(labels)):
        cumulative_s += max(segments[index - 1].duration_ms, 1) / 1000
        out = f"xf{index}" if index < len(labels) - 1 else "vjoined"
        filters.append(
            f"[{previous}][{labels[index]}]xfade=transition=fade"
            f":duration={transition_s:.3f}:offset={cumulative_s:.3f}[{out}]"
        )
        previous = out
    return filters, "vjoined"


# --- audio graph -----------------------------------------------------------


@dataclass(frozen=True)
class AudioSources:
    """Everything that could end up on the audio track.

    `narration` is required. `score` is what `music_generation` produced and
    `local` is what the user uploaded; both are optional, and a cut may carry
    either, both or neither.
    """

    narration: bytes
    score: bytes | None = None
    local: bytes | None = None


def _audio_filters(
    manifest: VideoEditManifest,
    sources: AudioSources,
    *,
    narration_input: int,
    score_input: int | None,
    local_input: int | None,
    trim_start_ms: int,
    trim_end_ms: int,
    lead_in_ms: int,
    total_ms: int,
) -> tuple[list[str], str]:
    """The mix, as a filter chain ending on one label.

    Built rather than fixed because which inputs exist varies per cut. The
    narration is always first into `amix` so `duration=first` measures against
    it, and every bed is padded and trimmed to the cut's length so a looping
    track cannot run past the picture.
    """
    filters: list[str] = []
    total_s = total_ms / 1000

    # Narration: cut to the trim window, then pushed back behind the title card.
    narration = (
        f"[{narration_input}:a]atrim=start={trim_start_ms / 1000:.3f}"
        f":end={trim_end_ms / 1000:.3f},asetpts=PTS-STARTPTS,aresample=44100"
    )
    if lead_in_ms > 0:
        narration += f",adelay=delays={lead_in_ms}:all=1"
    if manifest.audio.narration_gain_db:
        narration += f",volume={manifest.audio.narration_gain_db:.2f}dB"
    filters.append(f"{narration},apad,atrim=end={total_s:.3f}[narr]")

    beds: list[str] = []

    if score_input is not None and manifest.audio.keep_score and sources.score:
        bed = f"[{score_input}:a]aresample=44100"
        if manifest.audio.score_gain_db:
            bed += f",volume={manifest.audio.score_gain_db:.2f}dB"
        filters.append(f"{bed},apad,atrim=end={total_s:.3f},asetpts=PTS-STARTPTS[score]")
        beds.append("score")

    if local_input is not None and sources.local:
        offset = manifest.audio.local_offset_ms
        local = f"[{local_input}:a]aresample=44100"
        if offset < 0:
            # Negative offset starts the track partway in, which is how a listener
            # skips an intro to land a downbeat on the first line.
            local += f",atrim=start={abs(offset) / 1000:.3f},asetpts=PTS-STARTPTS"
        local += f",volume={manifest.audio.local_gain_db:.2f}dB"
        if offset > 0:
            local += f",adelay=delays={offset}:all=1"
        filters.append(f"{local},apad,atrim=end={total_s:.3f},asetpts=PTS-STARTPTS[local]")
        beds.append("local")

    if not beds:
        filters.append(f"[narr]{LOUDNORM}[mixraw]")
        return _apply_fades(filters, "mixraw", manifest, total_ms)

    if manifest.audio.duck_under_narration:
        # One narration copy drives every sidechain and another is mixed in.
        # An ffmpeg pad can only be consumed once, so the split is required.
        filters.append(f"[narr]asplit={len(beds) + 1}[narrmix]" + "".join(
            f"[narrsc{index}]" for index in range(len(beds))
        ))
        for index, bed in enumerate(beds):
            filters.append(
                f"[{bed}][narrsc{index}]sidechaincompress="
                "level_in=1:threshold=0.02:ratio=6:attack=80:release=600:mix=1"
                f"[{bed}d]"
            )
        mixed = ["narrmix", *[f"{bed}d" for bed in beds]]
    else:
        mixed = ["narr", *beds]

    joined = "".join(f"[{label}]" for label in mixed)
    filters.append(
        f"{joined}amix=inputs={len(mixed)}:duration=first:dropout_transition=0:normalize=0[mixraw]"
    )
    filters.append(f"[mixraw]{LOUDNORM}[mixnorm]")
    return _apply_fades(filters, "mixnorm", manifest, total_ms)


def _apply_fades(
    filters: list[str], label: str, manifest: VideoEditManifest, total_ms: int
) -> tuple[list[str], str]:
    fade_in = manifest.audio.fade_in_ms
    fade_out = manifest.audio.fade_out_ms
    if not fade_in and not fade_out:
        return filters, label

    chain = []
    if fade_in:
        chain.append(f"afade=t=in:st=0:d={fade_in / 1000:.3f}")
    if fade_out:
        start_s = max((total_ms - fade_out) / 1000, 0)
        chain.append(f"afade=t=out:st={start_s:.3f}:d={fade_out / 1000:.3f}")
    filters.append(f"[{label}]{','.join(chain)}[aout]")
    return filters, "aout"


# --- render ----------------------------------------------------------------


def render_edit(
    manifest: VideoEditManifest,
    segments: list[Segment],
    sources: AudioSources,
    *,
    trim_start_ms: int,
    trim_end_ms: int,
) -> bytes:
    """Encode one cut and return the MP4 bytes.

    A single ffmpeg pass: the stills become moving segments, the segments join,
    the subtitle file burns over the join, and the mix is built alongside. Doing
    it in one pass rather than several avoids writing an intermediate video only
    to decode it again for the next step.
    """
    if not segments:
        raise VideoEditError("nothing to render")

    width, height = ASPECT_SIZES[manifest.aspect]
    lead_in_ms = segments[0].duration_ms if segments[0].image is None else 0
    total_ms = sum(segment.duration_ms for segment in segments)

    with tempfile.TemporaryDirectory(prefix="daastaan-edit-") as tmp:
        workdir = Path(tmp)
        inputs: list[str] = []

        for index, segment in enumerate(segments):
            if segment.image is None:
                # The card has to carry the same crossfade overlap the stills do.
                card_s = segment.duration_ms / 1000
                if len(segments) > 1:
                    card_s += manifest.motion.transition_ms / 1000
                inputs += [
                    "-f", "lavfi",
                    "-t", f"{card_s:.3f}",
                    "-i", f"color=c={TITLE_CARD_BG}:s={width}x{height}:r={FPS}",
                ]
                continue
            path = workdir / f"frame_{index:04d}.png"
            path.write_bytes(segment.image)
            inputs += ["-i", str(path)]

        filters, labels = _segment_video_filters(
            segments, manifest, width=width, height=height
        )
        chain, video_label = _xfade_chain(segments, labels, manifest)
        filters += chain

        narration_path = workdir / "narration.mp3"
        narration_path.write_bytes(sources.narration)
        narration_input = len(segments)
        inputs += ["-i", str(narration_path)]

        score_input: int | None = None
        if sources.score and manifest.audio.keep_score:
            score_path = workdir / "score.wav"
            score_path.write_bytes(sources.score)
            score_input = narration_input + 1
            # Looped so a thirty-second bed still covers a four-minute cut.
            inputs += ["-stream_loop", "-1", "-i", str(score_path)]

        local_input: int | None = None
        if sources.local:
            local_path = workdir / "local_audio"
            local_path.write_bytes(sources.local)
            local_input = (score_input if score_input is not None else narration_input) + 1
            if manifest.audio.local_loop:
                inputs += ["-stream_loop", "-1"]
            inputs += ["-i", str(local_path)]

        audio_filters, audio_label = _audio_filters(
            manifest,
            sources,
            narration_input=narration_input,
            score_input=score_input,
            local_input=local_input,
            trim_start_ms=trim_start_ms,
            trim_end_ms=trim_end_ms,
            lead_in_ms=lead_in_ms,
            total_ms=total_ms,
        )
        filters += audio_filters

        # Subtitles last, so captions sit over the joined picture rather than
        # being crossfaded along with the frame they started on.
        subtitle_path = workdir / "captions.ass"
        subtitle_path.write_text(
            build_ass(segments, manifest, width=width, height=height, total_ms=total_ms),
            encoding="utf-8",
        )
        has_text = _has_burned_text(manifest)
        if has_text:
            filters.append(
                f"[{video_label}]subtitles=filename={_escape_filter_path(str(subtitle_path))}"
                f"[vsub]"
            )
            video_label = "vsub"
        filters.append(f"[{video_label}]format=yuv420p[vout]")

        output = workdir / "cut.mp4"
        command = [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[vout]",
            "-map", f"[{audio_label}]",
            *VIDEO_CODEC,
            *AUDIO_CODEC,
            *MOVFLAGS,
            "-r", str(FPS),
            # An explicit duration rather than `-shortest`: the video runs a
            # transition longer than the mix, and `-shortest` would trim the
            # final line rather than pad the silence.
            "-t", f"{total_ms / 1000:.3f}",
            str(output),
        ]

        result = subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, timeout=RENDER_TIMEOUT_SECONDS
        )
        if result.returncode != 0:
            log.error("edit_render_failed", stderr=result.stderr[-2000:])
            raise VideoEditError(
                f"ffmpeg exited {result.returncode}: {result.stderr[-500:]}"
            )

        data = output.read_bytes()
        log.info(
            "edit_rendered",
            segments=len(segments),
            aspect=manifest.aspect.value,
            duration_ms=total_ms,
            bytes=len(data),
        )
        return data


def _has_burned_text(manifest: VideoEditManifest) -> bool:
    if manifest.caption.enabled:
        return True
    if manifest.watermark.enabled and manifest.watermark.text.strip():
        return True
    card = manifest.title_card
    return card.enabled and bool(card.heading.strip() or card.subheading.strip())


__all__ = [
    "AudioSources",
    "Segment",
    "VideoEditError",
    "build_ass",
    "plan_segments",
    "render_edit",
    "wrap_caption",
]
