"""Client and prompt builder for Dastaan's private local-music sidecar.

The Stable Audio MLX process deliberately lives outside this repository's
Docker image: it must run natively on Apple Silicon to use Metal.  This module
is the narrow boundary between a media worker and that host service.  It sends
only a bounded, instrumental music brief and stores no service credentials in
story state or logs.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import secrets
import time
import wave
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from daastaan_contracts import StoryState, limits


class MusicWorkerSettings(Protocol):
    music_service_base_url: str
    music_service_token: str | None
    music_service_hmac_secret: str | None
    music_service_timeout_seconds: int
    music_service_poll_interval_seconds: float
    music_duration_seconds: int


class MusicServiceError(RuntimeError):
    """A terminal error returned by, or while contacting, the local service."""


class MusicServiceUnavailable(MusicServiceError):
    """The host is offline or has not loaded the MLX model."""


@dataclass(frozen=True)
class MusicBrief:
    prompt: str
    negative_prompt: str
    duration_seconds: int
    seed: int


@dataclass(frozen=True)
class GeneratedMusic:
    audio: bytes
    duration_ms: int
    seed: int
    job_id: str


_NEGATIVE_PROMPT = (
    "vocals, singing, speech, spoken word, lyrics, humming, artist imitation, "
    "copyrighted melody"
)
_UNSAFE_DIRECTION = re.compile(
    r"\b(?:in\s+the\s+style\s+of|sound\s+like|inspired\s+by|"
    r"like\s+the\s+music\s+(?:of|from)|cover\s+of|artist|"
    r"copyrighted|recognizable\s+melod(?:y|ies)|lyrics?|vocals?|sing(?:ing)?)\b",
    re.IGNORECASE,
)


def _clean_phrase(value: str | None, *, limit: int) -> str:
    """Make model-derived direction safe and compact for the service boundary.

    Prompt text is never used in a shell command by Dastaan, but limiting it to
    printable prose avoids accidental data bloat and makes the external service
    receive a deliberately narrow music brief rather than the raw story.
    """
    if not value:
        return "unspecified"
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit] or "unspecified"


def _safe_feedback_direction(value: str) -> str:
    """Keep artistic feedback while refusing imitation/song/vocal direction."""
    cleaned = _clean_phrase(value, limit=240)
    if _UNSAFE_DIRECTION.search(cleaned):
        return "adjust the mood and instrumentation without imitating an artist, song, or vocals"
    return cleaned


def build_music_brief(state: StoryState, settings: MusicWorkerSettings) -> MusicBrief:
    """Build one global instrumental bed from already-derived story metadata.

    The first release intentionally produces one loopable bed.  The existing
    ffmpeg composer already loops a short asset under narration, and keeping the
    service request to one bounded cue makes a 16 GB Mac predictable.  Scene
    cues can later replace this without changing the HTTP security boundary.
    """
    mood = state.mood
    genre = _clean_phrase(mood.genre if mood else state.genre_hint, limit=80)
    mood_name = _clean_phrase(mood.mood if mood else None, limit=80)
    pacing = _clean_phrase(mood.pacing if mood else None, limit=60)
    tone_keywords = mood.tone_keywords if mood else []
    tones = ", ".join(_clean_phrase(tone, limit=40) for tone in tone_keywords[:5])
    scene_notes = "; ".join(
        _clean_phrase(f"{scene.title}: {scene.mood_tag}", limit=100)
        for scene in state.scenes[:4]
    )
    feedback = ""
    if state.regen and state.regen.scope == "music" and state.regen.instruction_delta:
        feedback_direction = _safe_feedback_direction(state.regen.instruction_delta)
        feedback = f" Listener adjustment: {feedback_direction}."

    prompt = (
        "Instrumental background score for an audio drama. "
        f"Genre: {genre}. Mood: {mood_name}. Tone: {tones or 'cinematic and restrained'}. "
        f"Pacing: {pacing}. Scene movement: {scene_notes or 'a cohesive story arc'}. "
        "Use a seamless, non-distracting texture that supports spoken narration. "
        "No vocals or recognizable melodies."
        f"{feedback}"
    )
    prompt = _clean_phrase(prompt, limit=limits.MUSIC_MAX_PROMPT_CHARS)
    # A retry must submit byte-identical data under the same idempotency key.
    # Deriving the seed from the version and finalized prompt achieves that while
    # still making a new music-only version intentionally produce a new cue.
    seed = int.from_bytes(
        hashlib.sha256(f"{state.version_id}:{prompt}".encode()).digest()[:4],
        byteorder="big",
    ) & 0x7FFFFFFF
    return MusicBrief(
        prompt=prompt,
        negative_prompt=_NEGATIVE_PROMPT,
        duration_seconds=settings.music_duration_seconds,
        seed=seed,
    )


def validate_wav(audio: bytes) -> int:
    """Validate a sidecar result before it reaches shared object storage."""
    if not audio or len(audio) > limits.MUSIC_MAX_ASSET_BYTES:
        raise MusicServiceError("music response has an invalid size")
    try:
        with wave.open(io.BytesIO(audio), "rb") as handle:
            channels = handle.getnchannels()
            sample_rate = handle.getframerate()
            sample_width = handle.getsampwidth()
            frames = handle.getnframes()
    except (EOFError, wave.Error) as exc:
        raise MusicServiceError("music response is not a valid WAV file") from exc

    if channels != 2 or sample_rate != 44_100 or sample_width != 2:
        raise MusicServiceError("music response must be 44.1 kHz, stereo, 16-bit WAV")
    duration_ms = int(frames / sample_rate * 1000)
    if not (
        limits.MUSIC_MIN_DURATION_SECONDS * 1000
        <= duration_ms
        <= (limits.MUSIC_MAX_DURATION_SECONDS + 2) * 1000
    ):
        raise MusicServiceError("music response duration is outside the allowed range")
    return duration_ms


class MusicServiceClient:
    """Synchronous, authenticated adapter used from a dedicated Celery worker."""

    def __init__(self, settings: MusicWorkerSettings) -> None:
        if not settings.music_service_token or not settings.music_service_hmac_secret:
            raise MusicServiceUnavailable("music service credentials are not configured")
        self.base_url = settings.music_service_base_url.rstrip("/")
        self.token = settings.music_service_token
        self.hmac_secret = settings.music_service_hmac_secret.encode("utf-8")
        self.timeout_seconds = settings.music_service_timeout_seconds
        self.poll_interval_seconds = settings.music_service_poll_interval_seconds
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            follow_redirects=False,
            # A signed private request must never inherit HTTP(S)_PROXY or
            # ALL_PROXY from an operator's shell/container environment.
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MusicServiceClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(
        self,
        method: str,
        path: str,
        body: bytes,
        *,
        idempotency_key: str = "",
    ) -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        body_hash = hashlib.sha256(body).hexdigest()
        signing_input = (
            f"{method.upper()}\n{path}\n{timestamp}\n{body_hash}\n{nonce}\n{idempotency_key}"
        ).encode()
        signature = hmac.new(self.hmac_secret, signing_input, hashlib.sha256).hexdigest()
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Daastaan-Timestamp": timestamp,
            "X-Daastaan-Nonce": nonce,
            "X-Daastaan-Signature": signature,
        }

    def _request(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        idempotency_key = (extra_headers or {}).get("Idempotency-Key", "")
        headers = self._headers(method, path, body, idempotency_key=idempotency_key)
        if extra_headers:
            headers.update(extra_headers)
        try:
            response = self._client.request(
                method,
                f"{self.base_url}{path}",
                content=body or None,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise MusicServiceUnavailable("local music service is unreachable") from exc

        if response.status_code == 503:
            raise MusicServiceUnavailable("local music service is not ready")
        if response.status_code >= 400:
            detail = response.text[:300]
            raise MusicServiceError(f"music service returned {response.status_code}: {detail}")
        return response

    def generate(self, *, idempotency_key: str, brief: MusicBrief) -> GeneratedMusic:
        payload: dict[str, Any] = {
            "prompt": brief.prompt,
            "negative_prompt": brief.negative_prompt,
            "duration_seconds": brief.duration_seconds,
            "seed": brief.seed,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        response = self._request(
            "POST",
            "/v1/music/jobs",
            body,
            extra_headers={
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
        )
        if response.status_code != 202:
            raise MusicServiceError(f"music job was not accepted: {response.status_code}")
        try:
            job_id = str(response.json()["job_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MusicServiceError("music service returned an invalid job response") from exc

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            status_response = self._request("GET", f"/v1/music/jobs/{job_id}")
            try:
                status = status_response.json()
                state = status["status"]
            except (KeyError, TypeError, ValueError) as exc:
                raise MusicServiceError("music service returned invalid job status") from exc
            if state == "succeeded":
                audio = self._request("GET", f"/v1/music/jobs/{job_id}/audio").content
                duration_ms = validate_wav(audio)
                return GeneratedMusic(
                    audio=audio,
                    duration_ms=duration_ms,
                    seed=int(status.get("seed", brief.seed)),
                    job_id=job_id,
                )
            if state in {"failed", "expired"}:
                error = _clean_phrase(str(status.get("error_code", "unknown_error")), limit=120)
                raise MusicServiceError(f"music job failed: {error}")
            time.sleep(self.poll_interval_seconds)
        raise MusicServiceUnavailable("local music service timed out")

    def delete_job(self, job_id: str) -> None:
        """Remove a sidecar result after durable central storage commits.

        Callers must *not* delete earlier: a worker crash between download and
        object-storage commit should be able to replay the durable sidecar job.
        """
        self._request("DELETE", f"/v1/music/jobs/{job_id}")
