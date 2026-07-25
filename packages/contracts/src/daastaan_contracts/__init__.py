"""Shared contracts for the Daastaan monorepo.

Imported by the API service, the agent service, and (via generated OpenAPI types)
the frontends. Deliberately depends only on Pydantic so that adding it to a
service never drags in a database driver or an LLM SDK.
"""

from .languages import language_name
from .enums import (
    AssetKind,
    CharacterRole,
    FeedbackStatus,
    JobStatus,
    LineType,
    Scope,
    StageName,
    StoryStatus,
    UserRole,
)
from .models import (
    Character,
    CharacterRegistryOutput,
    DialogueLine,
    DialogueScriptOutput,
    EmotionTaggingOutput,
    MediaAsset,
    MoodClassificationOutput,
    NarratorPersona,
    NarratorPersonaOutput,
    RegenDirective,
    Scene,
    StoryUnderstandingOutput,
    ValidatedDirective,
    VoiceAssignment,
)
from .stages import (
    AGENT_STAGES,
    FANOUT_STAGES,
    PIPELINE_STAGES,
    downstream_from,
    is_fanout,
    is_valid_entry,
    plan_stages,
    stage_index,
)
from .state import StoryState
from .tasks import STAGE_QUEUE, Queue, TaskName, progress_channel

__all__ = [
    "AGENT_STAGES",
    "language_name",
    "FANOUT_STAGES",
    "PIPELINE_STAGES",
    "STAGE_QUEUE",
    "AssetKind",
    "Character",
    "CharacterRegistryOutput",
    "CharacterRole",
    "DialogueLine",
    "DialogueScriptOutput",
    "EmotionTaggingOutput",
    "FeedbackStatus",
    "JobStatus",
    "LineType",
    "MediaAsset",
    "MoodClassificationOutput",
    "NarratorPersona",
    "NarratorPersonaOutput",
    "Queue",
    "RegenDirective",
    "Scene",
    "Scope",
    "StageName",
    "StoryState",
    "StoryStatus",
    "StoryUnderstandingOutput",
    "TaskName",
    "UserRole",
    "ValidatedDirective",
    "VoiceAssignment",
    "downstream_from",
    "is_fanout",
    "is_valid_entry",
    "plan_stages",
    "progress_channel",
    "stage_index",
]
