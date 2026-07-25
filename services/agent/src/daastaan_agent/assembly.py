"""Episode composition with ffmpeg.

Built as a single `filter_complex` rather than a sequence of shell steps: each
clip gets its trailing pause via `apad`, everything concatenates into one
narration track, an optional music bed is mixed underneath, and `loudnorm`
levels the result so lines synthesised in separate calls do not jump in volume.

ffmpeg is invoked with an argv list and never `shell=True`, and every path comes
from a temporary file this module created. No model output reaches the command
line - that is the whole reason the pipeline mints its own ids.
"""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

MUSIC_BED_VOLUME = 0.18
OUTPUT_BITRATE = "128k"
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


class FFmpegMissingError(RuntimeError):
    pass


class AssemblyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Clip:
    audio: bytes
    pause_after_ms: int = 0


@dataclass(frozen=True)
class SceneFrame:
    image: bytes
    duration_ms: int
    scene_id: str
    text: str = ""
    speaker: str = ""


def ffmpeg_path() -> str:
    """Absolute path to ffmpeg.

    Resolved rather than relying on PATH lookup at exec time, so the binary being
    run is unambiguous. `imageio-ffmpeg` ships a binary inside its wheel and is
    the fallback for environments where system packages cannot be installed.
    """
    if found := shutil.which("ffmpeg"):
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise FFmpegMissingError(
            "ffmpeg not found; install it in the image or add imageio-ffmpeg"
        ) from exc


def ffprobe_path() -> str:
    if found := shutil.which("ffprobe"):
        return found
    raise FFmpegMissingError("ffprobe not found; it ships with the ffmpeg package")


def probe_duration_ms(audio: bytes, suffix: str = ".mp3") -> int:
    """Duration of an audio buffer, used for TTS cost estimation.

    Falls back to a bitrate estimate rather than raising: this feeds cost
    accounting, and a slightly wrong number is much better than a failed task.
    """
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
        handle.write(audio)
        handle.flush()
        try:
            # Fixed argv list, absolute binary, and a path this process created.
            result = subprocess.run(  # noqa: S603
                [
                    ffprobe_path(), "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    handle.name,
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
            return int(float(result.stdout.strip()) * 1000)
        except Exception:
            log.warning("ffprobe_failed_estimating_duration", size=len(audio))
            return int(len(audio) / 4000)  # ~32 kbps mp3


def compose_episode(clips: list[Clip], music_bed: bytes | None = None) -> bytes:
    if not clips:
        raise AssemblyError("cannot assemble an episode with no audio clips")

    with tempfile.TemporaryDirectory(prefix="daastaan-asm-") as tmp:
        workdir = Path(tmp)
        inputs: list[str] = []
        filters: list[str] = []

        for index, clip in enumerate(clips):
            path = workdir / f"clip_{index:04d}.mp3"
            path.write_bytes(clip.audio)
            inputs += ["-i", str(path)]
            pad = max(clip.pause_after_ms, 0) / 1000
            filters.append(f"[{index}:a]apad=pad_dur={pad:.3f},aresample=44100[a{index}]")

        concat_inputs = "".join(f"[a{i}]" for i in range(len(clips)))
        filters.append(f"{concat_inputs}concat=n={len(clips)}:v=0:a=1[narration]")

        if music_bed:
            # The local Stable Audio sidecar returns validated PCM WAV.  ffmpeg
            # detects the format from the file header, but the suffix matters
            # for diagnostics and avoids claiming a WAV is an MP3.
            bed_path = workdir / "bed.wav"
            bed_path.write_bytes(music_bed)
            # -stream_loop repeats the bed so a short track still covers a long
            # episode; duration=first ends the mix when the narration ends.
            inputs += ["-stream_loop", "-1", "-i", str(bed_path)]
            filters.append(f"[{len(clips)}:a]volume={MUSIC_BED_VOLUME},aresample=44100[bed]")
            filters.append(
                "[narration][bed]amix=inputs=2:duration=first:dropout_transition=0[mixed]"
            )
            filters.append(f"[mixed]{LOUDNORM}[out]")
        else:
            filters.append(f"[narration]{LOUDNORM}[out]")

        output = workdir / "episode.mp3"
        command = [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[out]",
            "-c:a", "libmp3lame", "-b:a", OUTPUT_BITRATE,
            str(output),
        ]

        # Argv list, never shell=True. Every path is a temp file created above and
        # every id in it came from `daastaan_common.ids`, so no model output can
        # reach this command line.
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)  # noqa: S603
        if result.returncode != 0:
            log.error("ffmpeg_failed", stderr=result.stderr[-2000:])
            raise AssemblyError(f"ffmpeg exited {result.returncode}: {result.stderr[-500:]}")

        log.info("episode_assembled", clips=len(clips), music=bool(music_bed))
        return output.read_bytes()


# --- export transcodes -----------------------------------------------------

# Every export is derived from the MP3 master, which is itself lossy at 128 kbps.
# Nothing here recovers quality that the master does not have: FLAC and WAV are
# lossless *containers* around a lossy source and are only useful for editing,
# and the lossy targets are set high enough that a second generation of encoding
# is not audible. MP3 is absent on purpose - the master is already MP3, and
# re-encoding it to itself would lose quality for nothing.
AUDIO_EXPORTS: dict[str, tuple[list[str], str, str]] = {
    "m4a": (["-c:a", "aac", "-b:a", "192k"], "m4a", "audio/mp4"),
    "opus": (["-c:a", "libopus", "-b:a", "96k"], "opus", "audio/ogg"),
    "flac": (["-c:a", "flac"], "flac", "audio/flac"),
    "wav": (["-c:a", "pcm_s16le"], "wav", "audio/wav"),
}

# The master, described in the same shape so callers can look up any format.
MASTER_FORMAT = "mp3"
MASTER_CONTENT_TYPE = "audio/mpeg"


def export_content_type(fmt: str) -> str:
    if fmt == MASTER_FORMAT:
        return MASTER_CONTENT_TYPE
    return AUDIO_EXPORTS[fmt][2]


def transcode(audio: bytes, fmt: str) -> bytes:
    """Re-encode the episode master into another container.

    Same safety pattern as `compose_episode`: argv list, temp files this function
    created, and `fmt` is looked up in a fixed table rather than interpolated, so
    a caller cannot smuggle ffmpeg flags through it.
    """
    if fmt == MASTER_FORMAT:
        return audio
    if fmt not in AUDIO_EXPORTS:
        raise AssemblyError(f"unsupported export format: {fmt}")

    codec_args, extension, _ = AUDIO_EXPORTS[fmt]

    with tempfile.TemporaryDirectory(prefix="daastaan-exp-") as tmp:
        workdir = Path(tmp)
        source = workdir / f"master.{MASTER_FORMAT}"
        source.write_bytes(audio)
        output = workdir / f"episode.{extension}"

        command = [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source),
            *codec_args,
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)  # noqa: S603
        if result.returncode != 0:
            log.error("transcode_failed", fmt=fmt, stderr=result.stderr[-2000:])
            raise AssemblyError(f"ffmpeg exited {result.returncode}: {result.stderr[-500:]}")

        data = output.read_bytes()
        log.info("episode_transcoded", fmt=fmt, source_bytes=len(audio), output_bytes=len(data))
        return data


def build_scene_timeline(
    lines: list, audio_assets: dict,
) -> dict[str, tuple[int, int]]:
    """Calculate per-scene start/end timestamps from line audio durations.

    ``lines`` are the story's ``DialogueLine`` objects.  ``audio_assets`` maps
    ``line_id`` to an object with ``duration_ms`` (typically a ``MediaAsset``).

    Returns ``{scene_id: (start_ms, end_ms)}``.
    """
    from collections import defaultdict

    scenes: dict[str, list] = defaultdict(list)
    for line in lines:
        scenes[line.scene_id].append(line)

    timeline: dict[str, tuple[int, int]] = {}
    cursor_ms = 0
    for scene_id in sorted(scenes, key=lambda sid: min(l.index for l in scenes[sid])):
        scene_lines = sorted(scenes[scene_id], key=lambda l: l.index)
        scene_start = cursor_ms
        for line in scene_lines:
            asset = audio_assets.get(line.id)
            duration = asset.duration_ms if asset and asset.duration_ms else 0
            cursor_ms += duration + (line.pause_after_ms or 0)
        timeline[scene_id] = (scene_start, cursor_ms)

    return timeline


_DRAWTEXT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_DRAWTEXT_WRAP = 45


def _wrap_text(text: str, width: int = _DRAWTEXT_WRAP) -> str:
    """Wrap text at word boundaries to fit within width characters."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        needed = len(word) if not current else 1 + len(word)
        if current and length + needed > width:
            lines.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += needed
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def _escape_textfile_path(path: str) -> str:
    """Escape characters that are special in ffmpeg filter option values."""
    # In filter_complex, these characters have structural meaning and must be
    # escaped with a backslash when they appear in an option value.
    for ch in ("\\", "'", ":", ";", "[", "]"):
        path = path.replace(ch, f"\\{ch}")
    return path


ZOOMPAN_FPS = 25
XFADE_DURATION = 0.5
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720

# Zoompan motion presets, cycled across frames.
# Each is a (z_expr, x_expr, y_expr) tuple for the zoompan filter.
_ZOOM_PRESETS: list[tuple[str, str, str]] = [
    # zoom-in: slow zoom from 1.0 to 1.15
    ("min(zoom+0.0005,1.15)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    # zoom-out: start zoomed in, pull back
    ("if(eq(on,1),1.15,max(zoom-0.0005,1.0))", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    # pan-left: fixed zoom, pan from right to left
    ("1.1", "if(eq(on,1),iw/4,max(x-0.5,0))", "ih/2-(ih/zoom/2)"),
    # pan-right: fixed zoom, pan from left to right
    ("1.1", "if(eq(on,1),0,min(x+0.5,iw/4))", "ih/2-(ih/zoom/2)"),
]


def compose_video(audio: bytes, scene_frames: list[SceneFrame]) -> bytes:
    """Combine a final audio track with scene images into an MP4 video.

    Each image gets a Ken Burns zoompan effect. When there are multiple frames,
    consecutive clips are joined with crossfade transitions. Uses the same
    safety pattern as ``compose_episode``: temp directory, argv list, no
    ``shell=True``, no model output in paths.
    """
    if not scene_frames:
        raise AssemblyError("cannot compose video with no scene frames")

    with tempfile.TemporaryDirectory(prefix="daastaan-vid-") as tmp:
        workdir = Path(tmp)

        # Write audio
        audio_path = workdir / "audio.mp3"
        audio_path.write_bytes(audio)

        n = len(scene_frames)
        inputs: list[str] = []
        filters: list[str] = []

        # Phase 1: write images and create zoompan + drawtext filters
        for idx, frame in enumerate(scene_frames):
            img_path = workdir / f"frame_{idx:04d}.png"
            img_path.write_bytes(frame.image)
            inputs += ["-i", str(img_path)]

            duration_s = max(frame.duration_ms, 1) / 1000
            # Add overlap material for xfade (except last frame)
            if n > 1 and idx < n - 1:
                duration_s += XFADE_DURATION
            total_frames = int(duration_s * ZOOMPAN_FPS)

            z_expr, x_expr, y_expr = _ZOOM_PRESETS[idx % len(_ZOOM_PRESETS)]
            # Scale source to square so zoompan has room to pan, then output at target size
            zp_out = f"zp{idx}"
            filters.append(
                f"[{idx}:v]scale=1280:1280:force_original_aspect_ratio=increase,"
                f"crop=1280:1280,"
                f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
                f":d={total_frames}:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:fps={ZOOMPAN_FPS}"
                f"[{zp_out}]"
            )

            # Burned-in caption via drawtext (uses textfile to avoid escaping issues)
            if frame.text:
                caption = _wrap_text(frame.text)
                if frame.speaker:
                    caption = f"{frame.speaker}: {caption}"
                txt_path = workdir / f"caption_{idx:04d}.txt"
                txt_path.write_text(caption, encoding="utf-8")
                escaped_path = _escape_textfile_path(str(txt_path))
                df_out = f"df{idx}"
                filters.append(
                    f"[{zp_out}]drawtext=textfile={escaped_path}"
                    f":fontfile={_DRAWTEXT_FONT}"
                    f":fontsize=28:fontcolor=white:borderw=2:bordercolor=black"
                    f":x=(w-text_w)/2:y=h-th-50"
                    f":box=1:boxcolor=black@0.5:boxborderw=8[{df_out}]"
                )
        # Collect final per-frame labels for xfade chaining
        frame_labels: list[str] = []
        for idx, frame in enumerate(scene_frames):
            frame_labels.append(f"df{idx}" if frame.text else f"zp{idx}")

        # Phase 2: xfade transitions between consecutive clips
        if n == 1:
            last_label = frame_labels[0]
        else:
            cumulative_s = 0.0
            prev_label = frame_labels[0]
            for i in range(1, n):
                cumulative_s += max(scene_frames[i - 1].duration_ms, 1) / 1000
                out_label = f"xf{i - 1}" if i < n - 1 else "vout"
                filters.append(
                    f"[{prev_label}][{frame_labels[i]}]xfade=transition=fade"
                    f":duration={XFADE_DURATION}:offset={cumulative_s:.3f}[{out_label}]"
                )
                prev_label = out_label
            last_label = "vout"

        # Audio input is the last -i
        inputs += ["-i", str(audio_path)]

        output = workdir / "video.mp4"
        command = [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", f"[{last_label}]",
            "-map", f"{n}:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(output),
        ]

        result = subprocess.run(command, capture_output=True, text=True, timeout=900)  # noqa: S603
        if result.returncode != 0:
            log.error("ffmpeg_video_failed", stderr=result.stderr[-2000:])
            raise AssemblyError(f"ffmpeg exited {result.returncode}: {result.stderr[-500:]}")

        log.info("video_composed", frames=n)
        return output.read_bytes()
