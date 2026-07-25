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


class VoiceGender(StrEnum):
    """How a character should read, not what they are.

    `NEUTRAL` is a real answer rather than a refusal: narrators, choruses and
    non-human characters genuinely have no gendered timbre, and forcing them into
    one is what makes a cast sound like two people.
    """

    FEMININE = "feminine"
    MASCULINE = "masculine"
    NEUTRAL = "neutral"


class VoiceAge(StrEnum):
    CHILD = "child"
    YOUNG = "young"
    ADULT = "adult"
    ELDER = "elder"


class AssetKind(StrEnum):
    LINE_AUDIO = "line_audio"
    SCENE_IMAGE = "scene_image"
    MUSIC_BED = "music_bed"
    FINAL_EPISODE = "final_episode"
    FINAL_VIDEO = "final_video"
    # A transcode of FINAL_EPISODE for download. Kept apart from the episode so
    # the player always resolves the master and never picks up a variant.
    EPISODE_EXPORT = "episode_export"


class IngestStatus(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    CLEANING = "cleaning"
    READY = "ready"
    FAILED = "failed"
