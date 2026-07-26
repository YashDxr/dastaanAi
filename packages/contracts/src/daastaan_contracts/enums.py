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


class ConsistencyCheckStatus(StrEnum):
    """Lifecycle of a read-only Plot Hole Hunter request."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StoryStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"
    FLAGGED = "flagged"


class ReviewStatus(StrEnum):
    """Editorial state, deliberately separate from the generation lifecycle.

    A story can be technically ``ready`` while an operator still needs changes
    before it is released. Keeping that decision out of :class:`StoryStatus`
    makes the review queue reversible without confusing workers about whether a
    pipeline run succeeded.
    """

    PENDING = "pending"
    APPROVED = "approved"
    FLAGGED = "flagged"
    CHANGES_REQUESTED = "changes_requested"


class ReviewAction(StrEnum):
    """The small, explicit review transition surface available to admins."""

    APPROVE = "approve"
    FLAG = "flag"
    CHANGES_REQUESTED = "changes_requested"
    CLEAR_FLAG = "clear_flag"


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
    # A transcode of MUSIC_BED for download. Same pattern as EPISODE_EXPORT.
    BGM_EXPORT = "bgm_export"
    # A re-cut of the episode produced by the video editor. Kept apart from
    # FINAL_VIDEO so the studio player always resolves the pipeline's own master
    # and never picks up somebody's 9:16 social crop.
    EDITED_VIDEO = "edited_video"
    # An audio track the user uploaded to lay under a cut. Never generated, so it
    # is not carried over on a regeneration the way produced assets are.
    LOCAL_AUDIO = "local_audio"
    # AI-generated character portrait, one per character per version.
    CHARACTER_AVATAR = "character_avatar"


class IngestStatus(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    CLEANING = "cleaning"
    READY = "ready"
    FAILED = "failed"
