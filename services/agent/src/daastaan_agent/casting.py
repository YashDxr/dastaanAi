"""Choosing a voice for each character.

The previous version mapped `CharacterRole` to a voice through a four-entry
dictionary, so every supporting character drew from a five-voice rotation and the
leads always sounded the same across every story. Differentiation was left
entirely to the `instructions` string, which `gpt-4o-mini-tts` renders as changes
in pitch and pace - the same actor doing voices, which is exactly what listeners
reported hearing.

This module casts instead. Each character carries a vocal gender and age band
from the registry stage, and every character in a story gets a *distinct* voice
scored against those. The provider offers eleven voices and a story holds at most
`limits.MAX_CHARACTERS`, so uniqueness is always achievable.

The timbre descriptions are judgements made by ear. They are the one part of this
file that is genuinely subjective, which is why the whole catalogue is
overridable from the admin panel.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from daastaan_contracts import Character, CharacterRole, VoiceAge, VoiceGender

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Voice:
    id: str
    gender: VoiceGender
    age: VoiceAge
    # Appended to the character's instructions so the voice is pushed further
    # toward the part rather than left to carry it alone.
    timbre: str


# The gpt-4o-mini-tts roster.
VOICE_CATALOGUE: tuple[Voice, ...] = (
    Voice("alloy", VoiceGender.NEUTRAL, VoiceAge.ADULT, "even and unhurried"),
    Voice("ash", VoiceGender.MASCULINE, VoiceAge.ADULT, "grainy and grounded"),
    Voice("ballad", VoiceGender.MASCULINE, VoiceAge.ADULT, "lyrical and theatrical"),
    Voice("coral", VoiceGender.FEMININE, VoiceAge.ADULT, "warm and rounded"),
    Voice("echo", VoiceGender.MASCULINE, VoiceAge.ADULT, "dry and measured"),
    Voice("fable", VoiceGender.NEUTRAL, VoiceAge.ELDER, "lilting, a storyteller's cadence"),
    Voice("nova", VoiceGender.FEMININE, VoiceAge.YOUNG, "bright and quick"),
    Voice("onyx", VoiceGender.MASCULINE, VoiceAge.ELDER, "deep and weighted"),
    Voice("sage", VoiceGender.FEMININE, VoiceAge.ADULT, "calm and low"),
    Voice("shimmer", VoiceGender.FEMININE, VoiceAge.YOUNG, "light and airy"),
    Voice("verse", VoiceGender.MASCULINE, VoiceAge.YOUNG, "open and emotive"),
)

VOICES_BY_ID = {voice.id: voice for voice in VOICE_CATALOGUE}

# Sarvam bulbul:v3 roster — Hindi-native voices for Indian languages.
SARVAM_VOICE_CATALOGUE: tuple[Voice, ...] = (
    Voice("manan", VoiceGender.MASCULINE, VoiceAge.ADULT, "conversational and consistent"),
    Voice("shubh", VoiceGender.MASCULINE, VoiceAge.YOUNG, "conversational and friendly"),
    Voice("amit", VoiceGender.MASCULINE, VoiceAge.ADULT, "professional and steady"),
    Voice("ratan", VoiceGender.MASCULINE, VoiceAge.ELDER, "deep and authoritative"),
    Voice("rohan", VoiceGender.MASCULINE, VoiceAge.YOUNG, "open and energetic"),
    Voice("kabir", VoiceGender.MASCULINE, VoiceAge.ADULT, "warm and grounded"),
    Voice("ritu", VoiceGender.FEMININE, VoiceAge.ADULT, "expressive and emotional"),
    Voice("shreya", VoiceGender.FEMININE, VoiceAge.ADULT, "authoritative and clear"),
    Voice("ishita", VoiceGender.FEMININE, VoiceAge.YOUNG, "dynamic and lively"),
    Voice("priya", VoiceGender.FEMININE, VoiceAge.ADULT, "precise and measured"),
    Voice("simran", VoiceGender.FEMININE, VoiceAge.YOUNG, "bright and warm"),
)

SARVAM_VOICES_BY_ID = {voice.id: voice for voice in SARVAM_VOICE_CATALOGUE}

# Role affinity for Sarvam voices
_SARVAM_ROLE_AFFINITY: dict[CharacterRole, frozenset[str]] = {
    CharacterRole.NARRATOR: frozenset({"manan", "ritu", "amit"}),
    CharacterRole.ANTAGONIST: frozenset({"ratan", "shreya", "kabir"}),
    CharacterRole.PROTAGONIST: frozenset({"shubh", "ishita", "rohan"}),
    CharacterRole.SUPPORTING: frozenset(),
}

# Languages served by the Sarvam bulbul:v3 model.
SARVAM_LANGUAGES = frozenset({"hi", "bn", "ta", "te", "kn", "ml", "mr", "gu", "pa", "or", "ur"})

# Age is ordinal, so a mismatch should be scored by distance: casting an elder as
# an adult is a smaller error than casting them as a child.
_AGE_ORDER: dict[VoiceAge, int] = {
    VoiceAge.CHILD: 0,
    VoiceAge.YOUNG: 1,
    VoiceAge.ADULT: 2,
    VoiceAge.ELDER: 3,
}

# Small nudges, deliberately worth less than a gender match. They break ties in
# favour of the obvious reading without ever overriding what the story said.
_ROLE_AFFINITY: dict[CharacterRole, frozenset[str]] = {
    CharacterRole.NARRATOR: frozenset({"fable", "sage", "alloy"}),
    CharacterRole.ANTAGONIST: frozenset({"onyx", "ash", "echo"}),
    CharacterRole.PROTAGONIST: frozenset({"nova", "verse", "coral"}),
    CharacterRole.SUPPORTING: frozenset(),
}

GENDER_MATCH = 100
GENDER_NEUTRAL_NEAR = 40  # neutral character on a gendered voice, or the reverse
AGE_STEP_PENALTY = 12
ROLE_BONUS = 8

# Leads are cast first so they get the closest match, and a supporting character
# takes whatever is left rather than the other way round.
_ROLE_PRIORITY: dict[CharacterRole, int] = {
    CharacterRole.NARRATOR: 0,
    CharacterRole.PROTAGONIST: 1,
    CharacterRole.ANTAGONIST: 2,
    CharacterRole.SUPPORTING: 3,
}


def _gender_score(character: VoiceGender, voice: VoiceGender) -> int:
    if character == voice:
        return GENDER_MATCH
    # A neutral on either side is a usable compromise. Feminine against masculine
    # is not, and scores nothing.
    if VoiceGender.NEUTRAL in (character, voice):
        return GENDER_NEUTRAL_NEAR
    return 0


def score(
    character: Character,
    voice: Voice,
    role_affinity: dict[CharacterRole, frozenset[str]] | None = None,
) -> int:
    affinity = role_affinity if role_affinity is not None else _ROLE_AFFINITY
    value = _gender_score(character.gender, voice.gender)
    value -= abs(_AGE_ORDER[character.age] - _AGE_ORDER[voice.age]) * AGE_STEP_PENALTY
    if voice.id in affinity.get(character.role, frozenset()):
        value += ROLE_BONUS
    return value


def _use_sarvam(language: str) -> bool:
    """Whether the given language should use the Sarvam voice catalogue.

    Requires the Sarvam API key to be configured; without it, OpenAI voices
    are used with a language hint (existing fallback behaviour).
    """
    if language not in SARVAM_LANGUAGES:
        return False
    from daastaan_common import get_settings
    return bool(get_settings().sarvam_api_key)


def assign_voices(
    characters: list[Character],
    *,
    pins: dict[str, str] | None = None,
    unavailable: set[str] | None = None,
    language: str = "en",
) -> dict[str, Voice]:
    """One voice per character, keyed by character id.

    Deterministic: the same cast always produces the same assignment, so a
    regeneration does not silently recast the story between versions.

    `pins` maps a role name to a voice id (the existing `voice_presets` admin
    setting). A pinned voice is given to the first character in that role and
    withdrawn from the pool, rather than being handed to every character in the
    role - which is what previously collapsed a cast onto four voices.
    """
    if not characters:
        return {}

    use_sarvam = _use_sarvam(language)
    catalogue = SARVAM_VOICE_CATALOGUE if use_sarvam else VOICE_CATALOGUE
    role_affinity = _SARVAM_ROLE_AFFINITY if use_sarvam else _ROLE_AFFINITY

    available = {
        voice.id: voice
        for voice in catalogue
        if voice.id not in (unavailable or set())
    }
    assigned: dict[str, Voice] = {}

    order = sorted(
        enumerate(characters),
        key=lambda pair: (_ROLE_PRIORITY.get(pair[1].role, 9), pair[0]),
    )

    pins = pins or {}
    pinned_roles: set[str] = set()
    for _, character in order:
        role = character.role.value
        pin = pins.get(role)
        if role in pinned_roles or not pin:
            continue
        if voice := available.pop(pin, None):
            assigned[character.id] = voice
            pinned_roles.add(role)
        else:
            log.warning("voice_pin_unavailable", role=role, voice=pin)

    for _, character in order:
        if character.id in assigned:
            continue
        if not available:
            fallback = max(catalogue, key=lambda v: score(character, v, role_affinity))
            log.warning("voice_pool_exhausted", character=character.name, voice=fallback.id)
            assigned[character.id] = fallback
            continue

        best = max(available.values(), key=lambda v: (score(character, v, role_affinity), v.id))
        del available[best.id]
        assigned[character.id] = best

    return assigned


def describe(character: Character, voice: Voice, persona_note: str | None) -> str:
    """The `instructions` string handed to TTS alongside the voice.

    The voice now carries the character's identity, so this describes the
    performance rather than trying to substitute for casting.
    """
    if character.role is CharacterRole.NARRATOR:
        return persona_note or "Natural, unhurried narration."

    age = "" if character.age is VoiceAge.ADULT else f"{character.age.value} "
    return (
        f"You are {character.name}, a {age}character. {character.personality} "
        f"Keep the delivery {voice.timbre}."
    ).strip()
