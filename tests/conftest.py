"""Test fixtures for isolated pipeline testing.

No external services required: SQLite in-memory for state, canned responses
for the gateway, and an in-memory dict for media storage.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock as _MagicMock

import pytest
from daastaan_contracts import (
    Character,
    CharacterRegistryOutput,
    CharacterRole,
    DialogueLine,
    DialogueScriptOutput,
    EmotionTaggingOutput,
    LineType,
    MoodClassificationOutput,
    NarratorPersona,
    NarratorPersonaOutput,
    Scene,
    StageName,
    StoryState,
    StoryUnderstandingOutput,
    VoiceAge,
    VoiceAssignment,
    VoiceGender,
)
from daastaan_contracts.models import (
    CharacterOutput,
    DialogueLineOutput,
    EmotionTagOutput,
    SceneOutput,
)
from sqlmodel import Session, SQLModel, create_engine

# --- In-memory database ---------------------------------------------------

# SQLite cannot render PostgreSQL JSONB columns.  For tests that need a real
# session with tables (e.g. testing repo functions), we swap JSONB → JSON at
# the SQLAlchemy level before creating tables.


def _patch_jsonb_for_sqlite():
    """Replace JSONB column types with generic JSON so SQLite can create the tables."""
    import sqlalchemy
    from daastaan_common import models  # noqa: F401 - triggers table registration
    from sqlalchemy.dialects.postgresql import JSONB

    for table in SQLModel.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = sqlalchemy.JSON()


@pytest.fixture
def in_memory_engine():
    _patch_jsonb_for_sqlite()
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def in_memory_session(in_memory_engine) -> Iterator[Session]:
    with Session(in_memory_engine) as session:
        yield session


@pytest.fixture
def stub_session() -> _MagicMock:
    """A lightweight mock session for node tests that don't touch the database.

    Most nodes only pass the session through to the gateway / admin-setting
    lookup, so a mock is sufficient and avoids the SQLite/JSONB workaround.
    """
    session = _MagicMock(spec=Session)
    session.get.return_value = None  # AdminSetting lookup returns None
    return session


# --- Mock store -----------------------------------------------------------


class InMemoryStore:
    """In-memory MediaStore for testing without filesystem or S3."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._data[key] = data
        return key

    def get(self, key: str) -> bytes:
        if key not in self._data:
            raise FileNotFoundError(f"no such key: {key}")
        return self._data[key]

    def exists(self, key: str) -> bool:
        return key in self._data


@pytest.fixture
def mock_store() -> InMemoryStore:
    return InMemoryStore()


# --- Mock gateway ---------------------------------------------------------

# Canned model outputs for each stage

CANNED_MOOD = MoodClassificationOutput(
    genre="fantasy",
    mood="whimsical",
    tone_keywords=["light", "magical", "warm"],
    pacing="moderate",
)

CANNED_STORY = StoryUnderstandingOutput(
    title="The Enchanted Garden",
    arc_summary="A child discovers a hidden garden and befriends its magical inhabitants.",
    setting="A secluded garden behind an old manor house",
    scenes=[
        SceneOutput(
            title="Discovery",
            summary="The child finds a hidden door in the garden wall.",
            setting="Overgrown stone wall with ivy",
            mood_tag="mysterious",
        ),
        SceneOutput(
            title="First Meeting",
            summary="The child meets a talking fox.",
            setting="A sun-dappled clearing",
            mood_tag="wonder",
        ),
    ],
)

CANNED_CHARACTERS = CharacterRegistryOutput(
    characters=[
        CharacterOutput(
            name="Narrator",
            role=CharacterRole.NARRATOR,
            personality="Warm, gentle storyteller.",
            sample_line="Once upon a time, in a garden forgotten by all...",
            gender=VoiceGender.NEUTRAL,
            age=VoiceAge.ELDER,
        ),
        CharacterOutput(
            name="Lily",
            role=CharacterRole.PROTAGONIST,
            personality="Curious and brave young girl.",
            sample_line="What's behind this door?",
            gender=VoiceGender.FEMININE,
            age=VoiceAge.CHILD,
        ),
        CharacterOutput(
            name="Rufus",
            role=CharacterRole.SUPPORTING,
            personality="Wise but mischievous talking fox.",
            sample_line="Ah, a visitor! How delightful.",
            gender=VoiceGender.MASCULINE,
            age=VoiceAge.ADULT,
        ),
    ],
)

CANNED_DIALOGUE = DialogueScriptOutput(
    lines=[
        DialogueLineOutput(
            scene_index=0,
            speaker="Narrator",
            text="The old garden wall stretched endlessly, covered in ivy.",
            line_type=LineType.NARRATION,
        ),
        DialogueLineOutput(
            scene_index=0,
            speaker="Lily",
            text="What's behind this door?",
            line_type=LineType.DIALOGUE,
        ),
        DialogueLineOutput(
            scene_index=1,
            speaker="Rufus",
            text="Ah, a visitor! How delightful.",
            line_type=LineType.DIALOGUE,
        ),
    ],
)

CANNED_EMOTIONS = EmotionTaggingOutput(
    tags=[
        EmotionTagOutput(
            line_id="line_0000",
            emotion="mysterious",
            intensity=2,
            tts_instructions="Slow, measured delivery with a sense of wonder.",
            pause_after_ms=800,
        ),
        EmotionTagOutput(
            line_id="line_0001",
            emotion="curious",
            intensity=3,
            tts_instructions="Bright, questioning tone.",
            pause_after_ms=400,
        ),
        EmotionTagOutput(
            line_id="line_0002",
            emotion="amused",
            intensity=3,
            tts_instructions="Warm, playful delivery with a slight chuckle.",
            pause_after_ms=600,
        ),
    ],
)

CANNED_NARRATOR = NarratorPersonaOutput(
    persona_name="The Garden Keeper",
    tone="Warm and inviting",
    pacing="Measured with gentle pauses",
    style_notes="British storytelling tradition, unhurried.",
    delivery_template="Speak as a wise grandparent telling a bedtime story.",
)


class MockGateway:
    """Gateway substitute that returns canned responses without API calls."""

    def __init__(self) -> None:
        self._responses: dict[type, Any] = {
            MoodClassificationOutput: CANNED_MOOD,
            StoryUnderstandingOutput: CANNED_STORY,
            CharacterRegistryOutput: CANNED_CHARACTERS,
            DialogueScriptOutput: CANNED_DIALOGUE,
            EmotionTaggingOutput: CANNED_EMOTIONS,
            NarratorPersonaOutput: CANNED_NARRATOR,
        }
        self.calls: list[dict[str, Any]] = []

    def structured(self, *, schema: type, system: str, user_content: str, **kwargs: Any) -> Any:
        self.calls.append({"method": "structured", "schema": schema.__name__, **kwargs})
        response = self._responses.get(schema)
        if response is None:
            raise ValueError(f"no canned response for {schema.__name__}")
        return response

    def moderate(self, text: str) -> None:
        self.calls.append({"method": "moderate", "text_len": len(text)})

    def speech(self, *, text: str, voice: str, instructions: str) -> tuple[bytes, int]:
        self.calls.append({"method": "speech", "voice": voice})
        # Return a tiny valid-ish MP3 (just bytes for testing)
        return b"\xff\xfb\x90\x00" * 100, 1500

    def image(self, *, prompt: str, size: str = "1024x1024") -> bytes:
        self.calls.append({"method": "image"})
        # Return tiny PNG header
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


@pytest.fixture
def mock_gateway() -> MockGateway:
    return MockGateway()


# --- Sample states --------------------------------------------------------


def _base_state(**overrides: Any) -> StoryState:
    defaults = {
        "story_id": "test-story-001",
        "version_id": "test-version-001",
        "user_id": "test-user-001",
        "raw_text": (
            "Once upon a time, there was a hidden garden behind an old manor. "
            "A curious girl named Lily discovered a secret door in the garden wall. "
            "Behind it, she met a talking fox named Rufus who showed her the wonders within."
        ),
    }
    defaults.update(overrides)
    return StoryState(**defaults)


@pytest.fixture
def sample_state() -> StoryState:
    """A fresh state, ready for stage 1."""
    return _base_state()


@pytest.fixture
def state_after_mood() -> StoryState:
    """State after mood_classification has run."""
    state = _base_state()
    state.mood = CANNED_MOOD
    state.completed_stages = [StageName.MOOD_CLASSIFICATION]
    return state


@pytest.fixture
def state_after_dialogue() -> StoryState:
    """State after dialogue_attribution: ready for emotion_tagging and narrator_persona."""
    state = _base_state()
    state.mood = CANNED_MOOD
    state.title = "The Enchanted Garden"
    state.arc_summary = "A child discovers a hidden garden."
    state.setting = "A secluded garden behind an old manor house"
    state.scenes = [
        Scene(
            id="scene_00", index=0, title="Discovery",
            summary="The child finds a hidden door.",
            setting="Stone wall", mood_tag="mysterious",
        ),
        Scene(
            id="scene_01", index=1, title="First Meeting",
            summary="The child meets a talking fox.",
            setting="Sun-dappled clearing", mood_tag="wonder",
        ),
    ]
    state.characters = [
        Character(
            id="char_00", name="Narrator", role=CharacterRole.NARRATOR,
            personality="Warm storyteller.", sample_line="Once upon a time...",
            gender=VoiceGender.NEUTRAL, age=VoiceAge.ELDER,
        ),
        Character(
            id="char_01", name="Lily", role=CharacterRole.PROTAGONIST,
            personality="Curious girl.", sample_line="What's behind this door?",
            gender=VoiceGender.FEMININE, age=VoiceAge.CHILD,
        ),
        Character(
            id="char_02", name="Rufus", role=CharacterRole.SUPPORTING,
            personality="Wise fox.", sample_line="Ah, a visitor!",
            gender=VoiceGender.MASCULINE, age=VoiceAge.ADULT,
        ),
    ]
    state.lines = [
        DialogueLine(
            id="line_0000", scene_id="scene_00", index=0, speaker="Narrator",
            character_id="char_00", text="The old garden wall stretched endlessly.",
            line_type=LineType.NARRATION,
        ),
        DialogueLine(
            id="line_0001", scene_id="scene_00", index=1, speaker="Lily",
            character_id="char_01", text="What's behind this door?",
            line_type=LineType.DIALOGUE,
        ),
        DialogueLine(
            id="line_0002", scene_id="scene_01", index=2, speaker="Rufus",
            character_id="char_02", text="Ah, a visitor! How delightful.",
            line_type=LineType.DIALOGUE,
        ),
    ]
    state.completed_stages = [
        StageName.MOOD_CLASSIFICATION,
        StageName.STORY_UNDERSTANDING,
        StageName.CHARACTER_REGISTRY,
        StageName.DIALOGUE_ATTRIBUTION,
    ]
    return state


@pytest.fixture
def state_fully_staged() -> StoryState:
    """State after all 7 reasoning stages: ready for media generation."""
    state = _base_state()
    state.mood = CANNED_MOOD
    state.title = "The Enchanted Garden"
    state.arc_summary = "A child discovers a hidden garden."
    state.setting = "A secluded garden behind an old manor house"
    state.scenes = [
        Scene(
            id="scene_00", index=0, title="Discovery",
            summary="The child finds a hidden door.",
            setting="Stone wall", mood_tag="mysterious",
        ),
        Scene(
            id="scene_01", index=1, title="First Meeting",
            summary="The child meets a talking fox.",
            setting="Sun-dappled clearing", mood_tag="wonder",
        ),
    ]
    state.characters = [
        Character(id="char_00", name="Narrator", role=CharacterRole.NARRATOR,
                  personality="Warm storyteller.", sample_line="Once upon a time...",
                  voice_preset="sage",
                  base_instructions="Speak as a wise grandparent telling a bedtime story."),
        Character(id="char_01", name="Lily", role=CharacterRole.PROTAGONIST,
                  personality="Curious girl.", sample_line="What's behind this door?",
                  voice_preset="nova",
                  base_instructions="Voice of Lily. Curious girl."),
        Character(id="char_02", name="Rufus", role=CharacterRole.SUPPORTING,
                  personality="Wise fox.", sample_line="Ah, a visitor!",
                  voice_preset="coral",
                  base_instructions="Voice of Rufus. Wise fox."),
    ]
    state.lines = [
        DialogueLine(id="line_0000", scene_id="scene_00", index=0, speaker="Narrator",
                     character_id="char_00", text="The old garden wall stretched endlessly.",
                     line_type=LineType.NARRATION, emotion="mysterious", intensity=2,
                     tts_instructions="Slow, measured delivery.", pause_after_ms=800),
        DialogueLine(id="line_0001", scene_id="scene_00", index=1, speaker="Lily",
                     character_id="char_01", text="What's behind this door?",
                     line_type=LineType.DIALOGUE, emotion="curious", intensity=3,
                     tts_instructions="Bright, questioning tone.", pause_after_ms=400),
        DialogueLine(id="line_0002", scene_id="scene_01", index=2, speaker="Rufus",
                     character_id="char_02", text="Ah, a visitor! How delightful.",
                     line_type=LineType.DIALOGUE, emotion="amused", intensity=3,
                     tts_instructions="Warm, playful delivery.", pause_after_ms=600),
    ]
    state.narrator_persona = NarratorPersona(
        persona_name="The Garden Keeper",
        tone="Warm and inviting",
        pacing="Measured with gentle pauses",
        style_notes="British storytelling tradition.",
        delivery_template="Speak as a wise grandparent telling a bedtime story.",
    )
    state.voice_map = [
        VoiceAssignment(character_id="char_00", voice_preset="sage",
                        base_instructions="Speak as a wise grandparent."),
        VoiceAssignment(character_id="char_01", voice_preset="nova",
                        base_instructions="Voice of Lily. Curious girl."),
        VoiceAssignment(character_id="char_02", voice_preset="coral",
                        base_instructions="Voice of Rufus. Wise fox."),
    ]
    state.completed_stages = list(StageName)[:7]
    return state
