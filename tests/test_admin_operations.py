"""Admin Operations v1: review safety, asset gates, and audited settings."""

import pytest
from daastaan_api.routers import admin as admin_routes
from daastaan_api.schemas import AdminSettingIn, StoryReviewRequest
from daastaan_common.models import AdminSetting, AuditLog, MediaAsset, Story, StoryVersion, User
from daastaan_contracts import (
    AssetKind,
    DialogueLine,
    LineType,
    ReviewAction,
    ReviewStatus,
    Scene,
    StoryState,
    StoryStatus,
)
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, select


def _reviewable_story(session: Session, *, output_format: str = "audio") -> tuple[Story, User]:
    # The routes under test do not authenticate this fixture, so no real-looking
    # credential should appear in the test data.
    unused_hash = type(session).__name__
    owner = User(id="story-owner", email="owner@example.com", password_hash=unused_hash)
    reviewer = User(
        id="reviewer", email="reviewer@example.com", password_hash=unused_hash, role="admin"
    )
    story = Story(
        id="story-review",
        user_id=owner.id,
        title="The ferry home",
        status=StoryStatus.READY,
    )
    state = StoryState(
        story_id=story.id,
        version_id="version-review",
        user_id=owner.id,
        raw_text="Mira takes the ferry home while a storm gathers over the mountain road.",
        output_format=output_format,
        scenes=[
            Scene(
                id="scene-1",
                index=0,
                title="The dock",
                summary="Mira arrives at the ferry dock.",
                setting="A foggy harbour",
                mood_tag="tense",
            )
        ],
        lines=[
            DialogueLine(
                id="line-1",
                scene_id="scene-1",
                index=0,
                speaker="Mira",
                text="I will take the ferry.",
                line_type=LineType.DIALOGUE,
            ),
            DialogueLine(
                id="line-2",
                scene_id="scene-1",
                index=1,
                speaker="Narrator",
                text="The horn sounded over the water.",
                line_type=LineType.NARRATION,
            ),
        ],
    )
    version = StoryVersion(
        id=state.version_id,
        story_id=story.id,
        state_json=state.model_dump(mode="json"),
    )
    story.current_version_id = version.id
    session.add_all([owner, reviewer, story, version])
    for line in state.lines:
        session.add(
            MediaAsset(
                version_id=version.id,
                dedupe_key=f"line_audio:{line.id}",
                kind=AssetKind.LINE_AUDIO.value,
                object_key=f"{line.id}.mp3",
                line_id=line.id,
                duration_ms=1_000,
            )
        )
    session.add(
        MediaAsset(
            version_id=version.id,
            dedupe_key="final_episode",
            kind=AssetKind.FINAL_EPISODE.value,
            object_key="episode.mp3",
            duration_ms=2_000,
        )
    )
    session.commit()
    return story, reviewer


def test_quality_distinguishes_required_finals_from_optional_bgm(
    in_memory_session: Session,
) -> None:
    story, _ = _reviewable_story(in_memory_session)

    quality = admin_routes.get_story_quality(story.id, in_memory_session)
    coverage = {item.kind: item for item in quality.coverage}

    assert quality.ready_for_review is True
    assert coverage[AssetKind.FINAL_EPISODE].required is True
    assert coverage[AssetKind.FINAL_EPISODE].missing == 0
    assert coverage[AssetKind.MUSIC_BED].required is False
    assert coverage[AssetKind.MUSIC_BED].missing == 1
    assert coverage[AssetKind.SCENE_IMAGE].required is False


def test_video_quality_requires_permitted_video_final(in_memory_session: Session) -> None:
    story, _ = _reviewable_story(in_memory_session, output_format="video")

    quality = admin_routes.get_story_quality(story.id, in_memory_session)
    coverage = {item.kind: item for item in quality.coverage}

    assert quality.ready_for_review is False
    assert coverage[AssetKind.FINAL_VIDEO].required is True
    assert coverage[AssetKind.FINAL_VIDEO].missing == 1
    assert coverage[AssetKind.SCENE_IMAGE].required is True


def test_review_actions_are_reversible_and_audited(in_memory_session: Session) -> None:
    story, reviewer = _reviewable_story(in_memory_session)

    flagged = admin_routes.review_story(
        story.id,
        StoryReviewRequest(action=ReviewAction.FLAG, note="Needs a rights review."),
        in_memory_session,
        reviewer,
    )
    assert flagged.flagged is True
    assert flagged.status == StoryStatus.FLAGGED
    assert flagged.review.status == ReviewStatus.FLAGGED

    cleared = admin_routes.review_story(
        story.id,
        StoryReviewRequest(action=ReviewAction.CLEAR_FLAG),
        in_memory_session,
        reviewer,
    )
    assert cleared.flagged is False
    assert cleared.status == StoryStatus.READY
    assert cleared.review.status == ReviewStatus.PENDING

    approved = admin_routes.review_story(
        story.id,
        StoryReviewRequest(action=ReviewAction.APPROVE, note="Checked the assembled episode."),
        in_memory_session,
        reviewer,
    )
    assert approved.review.status == ReviewStatus.APPROVED
    history = in_memory_session.exec(
        select(AuditLog).where(AuditLog.target_id == story.id).order_by(AuditLog.created_at)
    ).all()
    assert [entry.metadata_json["action"] for entry in history] == ["flag", "clear_flag", "approve"]
    assert history[0].metadata_json["before"]["story_status"] == StoryStatus.READY
    assert history[0].metadata_json["after"]["story_status"] == StoryStatus.FLAGGED


def test_review_requires_a_complete_ready_episode(in_memory_session: Session) -> None:
    story, reviewer = _reviewable_story(in_memory_session, output_format="video")

    with pytest.raises(HTTPException, match="complete required assets"):
        admin_routes.review_story(
            story.id,
            StoryReviewRequest(action=ReviewAction.APPROVE),
            in_memory_session,
            reviewer,
        )
    with pytest.raises(ValidationError, match="note is required"):
        StoryReviewRequest(action=ReviewAction.CHANGES_REQUESTED)


def test_runtime_settings_are_validated_and_keep_before_after_audit(
    in_memory_session: Session,
) -> None:
    _, reviewer = _reviewable_story(in_memory_session)

    created = admin_routes.upsert_setting(
        "budget_cap_usd",
        AdminSettingIn(value={"amount": 25}, reason="Demo guardrail"),
        in_memory_session,
        reviewer,
    )
    assert created.value == {"amount": 25}
    updated = admin_routes.upsert_setting(
        "budget_cap_usd",
        AdminSettingIn(value={"amount": 50}),
        in_memory_session,
        reviewer,
    )
    assert updated.value == {"amount": 50}
    setting = in_memory_session.get(AdminSetting, "budget_cap_usd")
    assert setting is not None and setting.value_json == {"amount": 50}
    audits = in_memory_session.exec(
        select(AuditLog)
        .where(AuditLog.target_type == "admin_setting")
        .order_by(AuditLog.created_at)
    ).all()
    assert audits[0].metadata_json == {
        "operation": "create",
        "previous_value": None,
        "new_value": {"amount": 25},
        "reason": "Demo guardrail",
    }
    assert audits[1].metadata_json["previous_value"] == {"amount": 25}

    with pytest.raises(HTTPException, match="unsupported setting"):
        admin_routes.upsert_setting(
            "not_a_setting", AdminSettingIn(value={}), in_memory_session, reviewer
        )
    with pytest.raises(HTTPException, match="model override"):
        admin_routes.upsert_setting(
            "models", AdminSettingIn(value={"unrecognised": "gpt-4o"}), in_memory_session, reviewer
        )
