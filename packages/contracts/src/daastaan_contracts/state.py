"""The LangGraph state object.

This is the single most important contract in the repo: every stage takes it and
returns it, and both services import this exact class. Changing a field is a
cross-service change, so agree on it as a team before editing.

Celery tasks pass a `version_id` and rehydrate this from the database rather than
serialising it through the broker, which keeps queue messages small and makes any
task safe to retry in isolation.
"""

from pydantic import BaseModel, ConfigDict, Field

from .enums import StageName
from .models import (
    Character,
    DialogueLine,
    MediaAsset,
    MoodClassificationOutput,
    NarratorPersona,
    Scene,
    ValidatedDirective,
    VoiceAssignment,
)


class StoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_id: str
    version_id: str
    user_id: str

    raw_text: str
    genre_hint: str | None = None

    mood: MoodClassificationOutput | None = None
    title: str | None = None
    arc_summary: str | None = None
    setting: str | None = None

    scenes: list[Scene] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    lines: list[DialogueLine] = Field(default_factory=list)
    narrator_persona: NarratorPersona | None = None
    voice_map: list[VoiceAssignment] = Field(default_factory=list)

    audio_assets: list[MediaAsset] = Field(default_factory=list)
    image_assets: list[MediaAsset] = Field(default_factory=list)
    final_episode_key: str | None = None

    # Set only on a regeneration run. Stages read it to narrow what they touch.
    regen: ValidatedDirective | None = None
    completed_stages: list[StageName] = Field(default_factory=list)

    def scene_by_id(self, scene_id: str) -> Scene | None:
        return next((s for s in self.scenes if s.id == scene_id), None)

    def character_by_id(self, character_id: str) -> Character | None:
        return next((c for c in self.characters if c.id == character_id), None)

    def line_by_id(self, line_id: str) -> DialogueLine | None:
        return next((line for line in self.lines if line.id == line_id), None)
