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
            bed_path = workdir / "bed.mp3"
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
