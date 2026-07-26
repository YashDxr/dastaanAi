"""Celery task names and queue routing.

The API dispatches by name with `send_task` rather than importing task functions.
That keeps LangGraph, OpenAI, and ffmpeg out of the API image entirely while
still giving both sides one shared definition of the wire format.
"""

from enum import StrEnum

from .enums import StageName


class Queue(StrEnum):
    AGENTS = "agents"
    MEDIA = "media"
    MUSIC = "music"
    ASSEMBLY = "assembly"


class TaskName(StrEnum):
    RUN_PIPELINE = "daastaan.pipeline.run"
    RUN_STAGE = "daastaan.stage.run"
    TTS_LINE = "daastaan.media.tts_line"
    GEN_IMAGE = "daastaan.media.gen_image"
    GEN_AVATAR = "daastaan.media.gen_avatar"
    GEN_MUSIC = "daastaan.music.generate"
    ASSEMBLE = "daastaan.assembly.compose"
    INTERPRET_FEEDBACK = "daastaan.feedback.interpret"
    CONSISTENCY_CHECK = "daastaan.story.consistency_check"
    REGENERATE = "daastaan.pipeline.regenerate"
    COMPOSE_VIDEO = "daastaan.video.compose"
    INGEST_EXTRACT = "daastaan.ingest.extract"
    EXPORT_AUDIO = "daastaan.export.audio"
    EXPORT_BGM = "daastaan.export.bgm"
    EXPORT_VIDEO = "daastaan.export.video"
    RENDER_VIDEO_EDIT = "daastaan.video.render_edit"
    WRITERS_ROOM = "daastaan.analysis.writers_room"
    CLIFFHANGER = "daastaan.analysis.cliffhanger"
    STORY_GENOME = "daastaan.analysis.story_genome"


STAGE_QUEUE: dict[StageName, Queue] = {
    StageName.MOOD_CLASSIFICATION: Queue.AGENTS,
    StageName.STORY_UNDERSTANDING: Queue.AGENTS,
    StageName.CHARACTER_REGISTRY: Queue.AGENTS,
    StageName.DIALOGUE_ATTRIBUTION: Queue.AGENTS,
    StageName.EMOTION_TAGGING: Queue.AGENTS,
    StageName.NARRATOR_PERSONA: Queue.AGENTS,
    StageName.VOICE_ASSIGNMENT: Queue.AGENTS,
    StageName.TTS_SYNTHESIS: Queue.MEDIA,
    StageName.IMAGE_GENERATION: Queue.MEDIA,
    StageName.MUSIC_GENERATION: Queue.MUSIC,
    StageName.ASSEMBLY: Queue.ASSEMBLY,
    StageName.VIDEO_COMPOSITION: Queue.ASSEMBLY,
}
