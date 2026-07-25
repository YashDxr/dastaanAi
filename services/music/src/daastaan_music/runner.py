"""Safe MLX CLI execution for one music job at a time."""

from __future__ import annotations

import hashlib
import math
import os
import platform
import shutil
import signal
import subprocess
import threading
import wave
from array import array
from pathlib import Path
from typing import Protocol

from .models import StoredMusicJob
from .settings import MusicServiceSettings


class ModelNotReady(RuntimeError):
    pass


_SM_MUSIC_WEIGHT_FILES = (
    "dit_sm-music_f16.npz",
    "same_s_decoder_f32.npz",
    "same_s_encoder_f32.npz",
    "t5gemma_f16.npz",
)


class MusicRunner(Protocol):
    def ready(self) -> bool: ...

    def generate(self, job: StoredMusicJob, output_path: Path) -> None: ...

    def cancel(self) -> None: ...


def validate_wav_file(path: Path) -> tuple[int, str]:
    """Validate exactly what the central agent will later accept."""
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() != 2:
                raise ValueError("expected stereo output")
            if handle.getframerate() != 44_100:
                raise ValueError("expected 44.1 kHz output")
            if handle.getsampwidth() != 2:
                raise ValueError("expected 16-bit output")
            duration_ms = int(handle.getnframes() / handle.getframerate() * 1000)
    except (EOFError, wave.Error, ValueError) as exc:
        raise ValueError("generated output is not a valid 44.1 kHz stereo WAV") from exc
    if not 5_000 <= duration_ms <= 62_000:
        raise ValueError("generated output duration is outside the allowed range")
    return duration_ms, hashlib.sha256(path.read_bytes()).hexdigest()


class MlxCliRunner:
    """Calls the official Stable Audio 3 MLX wrapper with a fixed model choice."""

    def __init__(self, settings: MusicServiceSettings) -> None:
        self.settings = settings
        self._process_lock = threading.Lock()
        self._active_process: subprocess.Popen[str] | None = None

    @property
    def executable(self) -> str | None:
        if self.settings.sa3_path:
            candidate = Path(self.settings.sa3_path).expanduser()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
            return None
        return shutil.which("sa3")

    def ready(self) -> bool:
        return (
            platform.system() == "Darwin"
            and platform.machine() in {"arm64", "arm64e"}
            and self.executable is not None
            and self._weights_ready()
        )

    def _weights_ready(self) -> bool:
        """Require the exact small-model files before accepting a job.

        `sa3` can lazily download missing weights, but a first user story must
        not trigger a multi-gigabyte download or return a misleading readyz.
        The official wrapper and its models directory live side by side.
        """
        executable = self.executable
        if executable is None:
            return False
        weights_dir = Path(executable).resolve().parent / "models" / "mlx"
        return all((weights_dir / filename).is_file() for filename in _SM_MUSIC_WEIGHT_FILES)

    def generate(self, job: StoredMusicJob, output_path: Path) -> None:
        executable = self.executable
        if not self.ready() or executable is None:
            raise ModelNotReady("Stable Audio MLX command is not ready on this Apple-Silicon host")

        # All model and output choices are fixed server-side. Prompt text is an
        # argv item, never a shell fragment, and output_path is minted locally.
        command = [
            executable,
            "--prompt",
            job.prompt,
            "--negative-prompt",
            job.negative_prompt,
            "--cfg",
            "3.0",
            "--dit",
            "sm-music",
            "--decoder",
            "same-s",
            "--seconds",
            str(job.duration_seconds),
            "--steps",
            "8",
            "--seed",
            str(job.seed),
            "--out",
            str(output_path),
        ]
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # A dedicated process group lets a controlled sidecar shutdown
            # terminate the MLX child rather than leaving it to consume unified
            # memory while the next sidecar instance starts.
            start_new_session=True,
        )
        with self._process_lock:
            self._active_process = process
        try:
            try:
                _, stderr = process.communicate(timeout=self.settings.job_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                self._terminate_process(process)
                _, stderr = process.communicate()
                raise TimeoutError("sa3 exceeded its configured job timeout") from exc
        finally:
            with self._process_lock:
                if self._active_process is process:
                    self._active_process = None

        if process.returncode != 0:
            raise RuntimeError(f"sa3 exited {process.returncode}: {stderr[-500:]}")
        if not output_path.is_file():
            raise RuntimeError("sa3 completed without producing a WAV output")

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        """End the whole MLX process group, tolerating a process already gone."""
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            process.terminate()

    def cancel(self) -> None:
        """Interrupt an active model process during controlled service shutdown."""
        with self._process_lock:
            process = self._active_process
        if process and process.poll() is None:
            self._terminate_process(process)


class MockToneRunner:
    """Deterministic test-only runner; it is never the default production path."""

    def ready(self) -> bool:
        return True

    def cancel(self) -> None:
        """The deterministic test runner has no subprocess to interrupt."""

    def generate(self, job: StoredMusicJob, output_path: Path) -> None:
        sample_rate = 44_100
        frame_count = sample_rate * job.duration_seconds
        frequency = 176 + (job.seed % 120)
        samples = array("h")
        amplitude = 300
        for index in range(frame_count):
            sample = int(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
            samples.extend((sample, sample))
        with wave.open(str(output_path), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(samples.tobytes())
