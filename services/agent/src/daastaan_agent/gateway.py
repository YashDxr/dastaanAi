"""The single path to every paid API call.

No node or task calls the OpenAI SDK directly. Routing everything through here is
what makes three separate guarantees hold at once:

* every call writes exactly one `cost_ledger` row, so the budget dashboard is
  complete by construction rather than by remembering to instrument each caller;
* the model for a stage is resolved from `admin_settings`, so the admin panel can
  swap GPT-4o for GPT-4o-mini mid-event without a redeploy - and critically, it is
  never read from a task payload, so a tampered message cannot change billing;
* instructions and untrusted user text are always passed as separate message
  roles, never concatenated into one string.
"""

import base64
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import structlog
from daastaan_common import cache, get_settings
from daastaan_common.models import AdminSetting, CostLedger
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError
from sqlmodel import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from . import pricing
from .assembly import probe_duration_ms
from .tracing import langfuse_span, trace_metadata

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

MODEL_OVERRIDE_KEY = "models"

# Part of the image cache key, so raising it later cannot serve cheaper artwork
# generated under the old setting.
IMAGE_QUALITY = "medium"

# A bad API key, a malformed request, or a content refusal will fail identically
# on every attempt. Retrying them burns a minute of wall clock and buries the
# actual cause under a stack of retry logs, which is the last thing you want when
# something breaks mid-event. Only genuinely transient failures are retried.
PERMANENT_FAILURES = (AuthenticationError, PermissionDeniedError, BadRequestError)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, PERMANENT_FAILURES):
        return False
    if isinstance(exc, RateLimitError | APIConnectionError | APITimeoutError):
        return True
    # Server-side and lock-contention statuses are worth another attempt.
    return isinstance(exc, APIStatusError) and exc.status_code in {408, 409, 429} | set(
        range(500, 600)
    )


_RETRY = retry(
    retry=retry_if_exception(_is_transient),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)


class ModerationBlocked(RuntimeError):
    def __init__(self, categories: list[str]) -> None:
        super().__init__(f"content blocked by moderation: {', '.join(categories) or 'unspecified'}")
        self.categories = categories


@dataclass(frozen=True)
class StreamedDelta:
    """One observation of a structured completion that is still being generated.

    `parsed` is the response schema filled in as far as the model has got: complete
    entries for the fields it has finished, absent or partial for the rest. It is
    `None` until enough JSON has arrived to parse a prefix at all.

    `tokens` is a *rough* count derived from the response text so far, and exists
    only to drive a liveness indicator. It is deliberately not what the cost ledger
    records - that uses the usage figures the API reports when the call ends.
    """

    parsed: Any | None
    tokens: int


# How often a caller's `on_delta` may run. Model tokens arrive far faster than a
# progress bus should be written to: a 900-token stage would be nearly a thousand
# Redis publishes unthrottled. Fast enough to feel live, slow enough to be cheap.
_DELTA_MIN_INTERVAL_S = 0.2

# Tokens are roughly four characters of JSON. Only ever used for the liveness
# counter above, never for billing.
_CHARS_PER_TOKEN = 4


class ModelGateway:
    def __init__(
        self,
        session: Session,
        *,
        stage: str,
        version_id: str | None = None,
        user_id: str | None = None,
        story_id: str | None = None,
        bypass_cache: bool = False,
        on_delta: Callable[["StreamedDelta"], None] | None = None,
    ) -> None:
        self.session = session
        self.stage = stage
        self.version_id = version_id
        self.user_id = user_id
        # Where mid-call progress goes, supplied by whoever is running this stage.
        # A plain callback rather than anything story-shaped: this class has no
        # business knowing that progress is published, only that someone wants it.
        self.on_delta = on_delta
        # Recorded on cache entries so an operator can tell what a hashed key
        # belongs to. It is never part of a cache key: the cache is global by
        # design, and keying on the story would make every hit impossible.
        self.story_id = story_id
        # Set for every stage a regeneration re-runs. Those stages were chosen
        # precisely because the user wants a different result, and some of them
        # send byte-identical inputs - a line respeak reaches `speech` with the
        # same text, voice and instructions - so a read-through cache would hand
        # back the exact take being replaced and the regeneration would appear to
        # do nothing at all.
        self.bypass_cache = bypass_cache
        self.settings = get_settings()
        self.client = OpenAI(api_key=self.settings.openai_api_key)

    # --- model resolution -------------------------------------------------

    def _resolve_model(self, kind: str) -> str:
        """`kind` is one of reasoning, light, tts, image.

        An admin override for this stage wins, then a global override for the
        kind, then the configured default.
        """
        defaults = {
            "reasoning": self.settings.model_reasoning,
            "light": self.settings.model_light,
            "tts": self.settings.model_tts,
            "image": self.settings.model_image,
        }
        setting = self.session.get(AdminSetting, MODEL_OVERRIDE_KEY)
        overrides = setting.value_json if setting else {}
        chosen = overrides.get(self.stage) or overrides.get(kind) or defaults[kind]
        return str(chosen)

    # --- accounting -------------------------------------------------------

    def _record(
        self,
        *,
        model: str,
        cost_usd: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        unit_count: float = 0.0,
        is_estimated: bool = False,
        cache_hit: bool = False,
    ) -> None:
        self.session.add(
            CostLedger(
                version_id=self.version_id,
                user_id=self.user_id,
                stage=self.stage,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                unit_count=unit_count,
                cost_usd=cost_usd,
                is_estimated=is_estimated,
                cache_hit=cache_hit,
            )
        )
        self.session.commit()
        log.info(
            "cached_call" if cache_hit else "paid_call",
            stage=self.stage,
            model=model,
            cost_usd=round(cost_usd, 5),
            estimated=is_estimated,
        )

    def _record_cache_hit(self, *, model: str, unit_count: float = 0.0) -> None:
        """A hit still writes a ledger row, at zero cost.

        Dropping the row would make the saving invisible: spend would simply be
        lower with nothing to attribute it to. With the row present the admin
        console can report how many calls the cache avoided.
        """
        self._record(model=model, cost_usd=0.0, unit_count=unit_count, cache_hit=True)

    # --- cache ------------------------------------------------------------

    def _cache_reads_enabled(self) -> bool:
        return cache.enabled() and not self.bypass_cache

    def _source(self, model: str, text: str) -> cache.Source:
        return cache.Source(
            stage=self.stage,
            model=model,
            story_id=self.story_id,
            version_id=self.version_id,
            text=text,
        )

    # --- calls ------------------------------------------------------------

    @_RETRY
    def structured(
        self,
        *,
        schema: type[T],
        system: str,
        user_content: str,
        kind: str = "light",
        temperature: float = 0.7,
        on_delta: Callable[[StreamedDelta], None] | None = None,
    ) -> T:
        """Strict-schema completion, streamed.

        `system` holds our fixed instructions and `user_content` holds untrusted
        text. They are separate message roles on purpose: nothing a user writes is
        ever interpolated into the instruction string.

        The response is streamed whether or not anyone is watching, so there is a
        single code path to reason about; the return value and the ledger row are
        identical either way. `on_delta` - or the one given to the constructor, which
        is how the pipeline wires this without every node opting in - receives the
        partially-parsed schema at most every `_DELTA_MIN_INTERVAL_S`, plus one final
        call with the completed object so a consumer never misses whatever arrived in
        the last few tokens.

        A raising `on_delta` must not lose a paid completion, so callbacks are
        isolated: reporting progress is strictly less important than returning the
        result that was just paid for.
        """
        model = self._resolve_model(kind)
        report = on_delta or self.on_delta
        # The schema is part of the key, so tightening a model's fields
        # invalidates its old entries instead of failing to parse them.
        key = cache.digest(
            "llm", model, temperature, system, user_content, schema.model_json_schema()
        )

        if self._cache_reads_enabled():
            hit = cache.get_llm(key)
            if hit is not None:
                try:
                    parsed_hit = schema.model_validate(hit)
                except ValidationError:
                    log.warning("cache_entry_unusable", stage=self.stage, model=model)
                else:
                    # No deltas on a hit, and none are wanted: there is no
                    # generation to watch, and the stage completes immediately.
                    self._record_cache_hit(model=model)
                    return parsed_hit

        with langfuse_span(
            name=f"structured:{self.stage}",
            metadata=trace_metadata(stage=self.stage, model=model),
        ) as span:
            with self.client.chat.completions.stream(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                response_format=schema,
                temperature=temperature,
                # Streaming omits the usage object unless it is asked for, and
                # without it every reasoning call would land in the ledger as an
                # estimate. The budget dashboard is only trustworthy while these
                # are real counts.
                stream_options={"include_usage": True},
            ) as stream:
                self._consume_deltas(stream, report)
                completion = stream.get_final_completion()

            usage = completion.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0
            cost = pricing.chat_cost(model, input_tokens, output_tokens)
            self._record(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
            span.update(metadata=trace_metadata(
                stage=self.stage, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cost_usd=cost,
            ))

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(f"model returned no parseable output for stage {self.stage}")

        cache.put_llm(key, parsed.model_dump(mode="json"), source=self._source(model, user_content))
        return parsed

    def _consume_deltas(
        self, stream: Any, on_delta: Callable[[StreamedDelta], None] | None
    ) -> None:
        """Drain a structured stream, forwarding throttled snapshots to `on_delta`.

        The stream has to be drained regardless of whether anyone is listening -
        that is what produces the final completion - so the no-listener case is
        just the loop without the reporting.
        """
        last_report = 0.0
        latest: StreamedDelta | None = None
        # Whether `latest` is a delta the throttle held back rather than delivered.
        withheld = False

        for event in stream:
            if event.type != "content.delta":
                continue
            latest = StreamedDelta(
                parsed=event.parsed,
                tokens=len(event.snapshot) // _CHARS_PER_TOKEN,
            )
            if on_delta is None:
                continue
            now = time.monotonic()
            if now - last_report < _DELTA_MIN_INTERVAL_S:
                withheld = True
                continue
            last_report = now
            withheld = False
            self._safe_delta(on_delta, latest)

        # The throttle usually swallows the last delta, which is the one holding the
        # finished object, so it is flushed here - a consumer should see every field
        # rather than everything bar the tail. Only when it was actually withheld:
        # re-delivering a delta already sent would report a short stream twice.
        if on_delta is not None and latest is not None and withheld:
            self._safe_delta(on_delta, latest)

    def _safe_delta(
        self, on_delta: Callable[[StreamedDelta], None], delta: StreamedDelta
    ) -> None:
        """Run a progress callback without letting it break the call.

        A failure here means the user's progress bar misses a frame. Letting it
        propagate would instead discard a completion that has already been paid
        for, and - because the exception would surface from inside the retry
        decorator - potentially pay for it again.
        """
        try:
            on_delta(delta)
        except Exception:
            log.warning("stream_delta_callback_failed", stage=self.stage, exc_info=True)

    @_RETRY
    def moderate(self, text: str) -> None:
        """Raises `ModerationBlocked` if the input is disallowed. Free, so it runs
        on user input before anything billable happens."""
        result = self.client.moderations.create(
            model=self.settings.model_moderation, input=text
        )
        entry = result.results[0]
        if entry.flagged:
            flagged = [
                name
                for name, value in entry.categories.model_dump().items()
                if value is True
            ]
            raise ModerationBlocked(flagged)

    @_RETRY
    def speech(self, *, text: str, voice: str, instructions: str) -> tuple[bytes, int]:
        """Returns (mp3 bytes, duration_ms).

        mp3 rather than wav because the assembly step uses ffmpeg, so there is no
        reason to move uncompressed audio around.
        """
        model = self._resolve_model("tts")
        key = cache.digest("tts", model, voice, text, instructions)

        if self._cache_reads_enabled():
            hit = cache.get_tts(key)
            if hit is not None:
                audio = cache.read_blob(hit)
                if audio is not None:
                    duration_ms = int(hit.meta.get("duration_ms") or 0) or probe_duration_ms(audio)
                    self._record_cache_hit(model=model, unit_count=duration_ms / 1000)
                    return audio, duration_ms

        with langfuse_span(
            name=f"speech:{self.stage}",
            metadata=trace_metadata(stage=self.stage, model=model),
        ) as span:
            response = self.client.audio.speech.create(
                model=model,
                voice=voice,  # type: ignore[arg-type]
                input=text,
                instructions=instructions,
                response_format="mp3",
            )
            audio = response.read()
            duration_ms = probe_duration_ms(audio)
            cost = pricing.tts_cost(duration_ms)

            self._record(
                model=model,
                cost_usd=cost,
                unit_count=duration_ms / 1000,
                is_estimated=True,  # the speech endpoint returns no usage object
            )
            span.update(metadata=trace_metadata(
                stage=self.stage, model=model,
                duration_ms=duration_ms, cost_usd=cost,
            ))
        cache.put_tts(
            key, data=audio, duration_ms=duration_ms, source=self._source(model, text)
        )
        return audio, duration_ms

    def sarvam_speech(self, *, text: str, voice: str, language: str) -> tuple[bytes, int]:
        """Sarvam bulbul:v3 TTS — returns (mp3_bytes, duration_ms)."""
        import httpx

        lang_code = language if "-" in language else f"{language}-IN"
        key = cache.digest("tts", "sarvam:bulbul:v3", voice, text, lang_code)

        if self._cache_reads_enabled():
            hit = cache.get_tts(key)
            if hit is not None:
                audio = cache.read_blob(hit)
                if audio is not None:
                    duration_ms = int(hit.meta.get("duration_ms") or 0) or probe_duration_ms(audio)
                    self._record_cache_hit(model="sarvam:bulbul:v3", unit_count=duration_ms / 1000)
                    return audio, duration_ms

        payload = {
            "text": text,
            "target_language_code": lang_code,
            "speaker": voice,
            "model": "bulbul:v3",
            "sample_rate": 24000,
            "enable_preprocessing": True,
        }
        headers = {
            "api-subscription-key": self.settings.sarvam_api_key,
            "Content-Type": "application/json",
        }
        resp = httpx.post(
            "https://api.sarvam.ai/text-to-speech",
            json=payload, headers=headers, timeout=60,
        )
        resp.raise_for_status()
        audio_b64 = resp.json()["audios"][0]
        wav_bytes = base64.b64decode(audio_b64)
        mp3_bytes = self._wav_to_mp3(wav_bytes)
        duration_ms = probe_duration_ms(mp3_bytes)

        self._record(
            model="sarvam:bulbul:v3", cost_usd=0.0,
            unit_count=duration_ms / 1000, is_estimated=True,
        )
        cache.put_tts(
            key, data=mp3_bytes, duration_ms=duration_ms,
            source=self._source("sarvam:bulbul:v3", text),
        )
        return mp3_bytes, duration_ms

    @staticmethod
    def _wav_to_mp3(wav_bytes: bytes) -> bytes:
        """Convert WAV to MP3 via ffmpeg subprocess."""
        import subprocess

        wav_path = write_temp(wav_bytes, suffix=".wav")
        mp3_path = wav_path.with_suffix(".mp3")
        try:
            subprocess.run(  # noqa: S603
                [
                    "ffmpeg", "-y", "-i", str(wav_path),
                    "-codec:a", "libmp3lame", "-q:a", "2",
                    str(mp3_path),
                ],
                capture_output=True, check=True,
            )
            return mp3_path.read_bytes()
        finally:
            wav_path.unlink(missing_ok=True)
            mp3_path.unlink(missing_ok=True)

    @_RETRY
    def image(self, *, prompt: str, size: str = "1024x1024") -> bytes:
        model = self._resolve_model("image")
        key = cache.digest("image", model, prompt, size, IMAGE_QUALITY)

        if self._cache_reads_enabled():
            hit = cache.get_image(key)
            if hit is not None:
                image = cache.read_blob(hit)
                if image is not None:
                    self._record_cache_hit(model=model, unit_count=1)
                    return image

        with langfuse_span(
            name=f"image:{self.stage}",
            metadata=trace_metadata(stage=self.stage, model=model),
        ) as span:
            response = self.client.images.generate(
                model=model, prompt=prompt, size=size, n=1, quality=IMAGE_QUALITY  # type: ignore[arg-type]
            )
            payload = response.data[0].b64_json
            if not payload:
                raise ValueError("image response contained no data")

            cost = pricing.image_cost(1)
            self._record(model=model, cost_usd=cost, unit_count=1)
            span.update(metadata=trace_metadata(
                stage=self.stage, model=model, cost_usd=cost,
            ))
        image = base64.b64decode(payload)
        cache.put_image(key, data=image, source=self._source(model, prompt))
        return image


def write_temp(data: bytes, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    handle.write(data)
    handle.close()
    return Path(handle.name)
