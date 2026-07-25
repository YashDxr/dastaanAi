from enum import StrEnum


class StageName(StrEnum):
    """Pipeline stages. Values are persisted and appear in task payloads, so treat
    them as a wire format: renaming one is a breaking change."""

    MOOD_CLASSIFICATION = "mood_classification"
    STORY_UNDERSTANDING = "story_understanding"
    CHARACTER_REGISTRY = "character_registry"
    DIALOGUE_ATTRIBUTION = "dialogue_attribution"
    EMOTION_TAGGING = "emotion_tagging"
    NARRATOR_PERSONA = "narrator_persona"
    VOICE_ASSIGNMENT = "voice_assignment"
    TTS_SYNTHESIS = "tts_synthesis"
    IMAGE_GENERATION = "image_generation"
    MUSIC_GENERATION = "music_generation"
    ASSEMBLY = "assembly"
    VIDEO_COMPOSITION = "video_composition"


class Scope(StrEnum):
    """Blast radius of a regeneration request."""

    LINE = "line"
    CHARACTER = "character"
    SCENE = "scene"
    MUSIC = "music"
    FULL_STORY = "full_story"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class FeedbackStatus(StrEnum):
    """Outcome of interpreting one piece of free-text feedback.

    Interpretation can fail on a decision rather than an outage - moderation
    refusing the text, or the model naming a stage that is not a legal entry
    point - so the attempt needs a durable outcome the user can be shown.
    """

    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"


class StoryStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    FLAGGED = "flagged"


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class LineType(StrEnum):
    NARRATION = "narration"
    DIALOGUE = "dialogue"


class CharacterRole(StrEnum):
    NARRATOR = "narrator"
    PROTAGONIST = "protagonist"
    ANTAGONIST = "antagonist"
    SUPPORTING = "supporting"


class AssetKind(StrEnum):
    LINE_AUDIO = "line_audio"
    SCENE_IMAGE = "scene_image"
    MUSIC_BED = "music_bed"
    FINAL_EPISODE = "final_episode"
    FINAL_VIDEO = "final_video"
