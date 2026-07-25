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
from pathlib import Path
from typing import TypeVar

import structlog
from daastaan_common import get_settings
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
from pydantic import BaseModel
from sqlmodel import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from . import pricing
from .assembly import probe_duration_ms

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

MODEL_OVERRIDE_KEY = "models"

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


class ModelGateway:
    def __init__(
        self,
        session: Session,
        *,
        stage: str,
        version_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.session = session
        self.stage = stage
        self.version_id = version_id
        self.user_id = user_id
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
            )
        )
        self.session.commit()
        log.info(
            "paid_call",
            stage=self.stage,
            model=model,
            cost_usd=round(cost_usd, 5),
            estimated=is_estimated,
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
    ) -> T:
        """Strict-schema completion.

        `system` holds our fixed instructions and `user_content` holds untrusted
        text. They are separate message roles on purpose: nothing a user writes is
        ever interpolated into the instruction string.
        """
        model = self._resolve_model(kind)
        completion = self.client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            response_format=schema,
            temperature=temperature,
        )

        usage = completion.usage
        self._record(
            model=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cost_usd=pricing.chat_cost(
                model,
                usage.prompt_tokens if usage else 0,
                usage.completion_tokens if usage else 0,
            ),
        )

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError(f"model returned no parseable output for stage {self.stage}")
        return parsed

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
        response = self.client.audio.speech.create(
            model=model,
            voice=voice,  # type: ignore[arg-type]
            input=text,
            instructions=instructions,
            response_format="mp3",
        )
        audio = response.read()
        duration_ms = probe_duration_ms(audio)

        self._record(
            model=model,
            cost_usd=pricing.tts_cost(duration_ms),
            unit_count=duration_ms / 1000,
            is_estimated=True,  # the speech endpoint returns no usage object
        )
        return audio, duration_ms

    @_RETRY
    def image(self, *, prompt: str, size: str = "1024x1024") -> bytes:
        model = self._resolve_model("image")
        response = self.client.images.generate(
            model=model, prompt=prompt, size=size, n=1, quality="medium"  # type: ignore[arg-type]
        )
        payload = response.data[0].b64_json
        if not payload:
            raise ValueError("image response contained no data")

        self._record(model=model, cost_usd=pricing.image_cost(1), unit_count=1)
        return base64.b64decode(payload)


def write_temp(data: bytes, suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    handle.write(data)
    handle.close()
    return Path(handle.name)
