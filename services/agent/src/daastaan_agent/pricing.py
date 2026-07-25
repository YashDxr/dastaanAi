"""Cost estimation for paid API calls.

Chat costs come from the `usage` object the API returns. TTS cannot: the
`/v1/audio/speech` endpoint returns raw audio with no usage payload, so its cost
is derived from output duration instead. Rows written that way are flagged
`is_estimated` so the admin dashboard never overstates its own accuracy.

Verify these rates against current OpenAI pricing at the start of the build - they
move, and the budget dashboard is only as good as this table.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatRate:
    input_per_1m: float
    output_per_1m: float


CHAT_RATES: dict[str, ChatRate] = {
    "gpt-4o": ChatRate(2.50, 10.00),
    "gpt-4o-mini": ChatRate(0.15, 0.60),
}

# gpt-4o-mini-tts bills text input and audio output tokens separately; audio
# output dominates. OpenAI publishes roughly $0.015 per minute of audio.
TTS_USD_PER_MINUTE = 0.015

# gpt-image-1, 1024x1024 at medium quality.
IMAGE_USD_PER_IMAGE = 0.04

MODERATION_USD = 0.0  # omni-moderation-latest is free


def chat_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rate = CHAT_RATES.get(model)
    if rate is None:
        # Unknown model: bill at the most expensive known rate rather than zero,
        # so an unrecognised override cannot silently hide spend.
        rate = max(CHAT_RATES.values(), key=lambda r: r.output_per_1m)
    return (input_tokens / 1_000_000) * rate.input_per_1m + (
        output_tokens / 1_000_000
    ) * rate.output_per_1m


def tts_cost(duration_ms: int) -> float:
    return (duration_ms / 60_000) * TTS_USD_PER_MINUTE


def image_cost(count: int = 1) -> float:
    return count * IMAGE_USD_PER_IMAGE
