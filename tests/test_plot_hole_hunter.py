"""Focused tests for the read-only Plot Hole Hunter flow."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from daastaan_agent.consistency import consistency_payload, review_story_consistency
from daastaan_agent.tasks import check_story_consistency
from daastaan_api.routers.consistency import (
    create_consistency_check,
    list_consistency_checks,
)
from daastaan_common.models import ConsistencyCheck, Story, StoryVersion, User
from daastaan_contracts import (
    ConsistencyAnalysisOutput,
    ConsistencyCheckStatus,
    ConsistencyFindingOutput,
    Queue,
    StoryStatus,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import select


class ReviewGateway:
    def __init__(self, result: ConsistencyAnalysisOutput) -> None:
        self.result = result
        self.calls: list[dict] = []

    def structured(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self.result


def _result(*findings: ConsistencyFindingOutput) -> ConsistencyAnalysisOutput:
    return ConsistencyAnalysisOutput(summary="A concise review.", findings=list(findings))


def _persist_ready_story(session, state, *, version_id: str | None = None):  # type: ignore[no-untyped-def]
    """Make one fixture state look like a completed, owned story version."""
    version_id = version_id or state.version_id
    stored = state.model_copy(deep=True)
    stored.version_id = version_id
    user = User(
        id=state.user_id,
        email=f"{state.user_id}@example.test",
        password_hash=state.user_id,
    )
    story = Story(
        id=state.story_id,
        user_id=user.id,
        title=stored.title,
        status=StoryStatus.READY.value,
        current_version_id=version_id,
    )
    version = StoryVersion(
        id=version_id,
        story_id=story.id,
        version_number=1,
        state_json=stored.model_dump(mode="json"),
    )
    session.add(user)
    session.add(story)
    session.add(version)
    session.commit()
    return user, story, version, stored


def test_payload_contains_only_generated_structure(state_after_dialogue) -> None:
    state_after_dialogue.raw_text = "UNTRUSTED SOURCE TEXT MUST NOT REACH THIS CHECKER"

    payload = consistency_payload(state_after_dialogue)

    assert "UNTRUSTED SOURCE TEXT" not in payload
    assert '"scenes"' in payload
    assert state_after_dialogue.lines[0].id in payload


def test_review_maps_only_known_references(state_after_dialogue) -> None:
    gateway = ReviewGateway(
        _result(
            ConsistencyFindingOutput(
                severity="warning",
                type="timeline",
                scene_index=0,
                line_id="line_0001",
                explanation=(
                    "Lily asks about the door after the outline says she already opened it."
                ),
                suggestion="Move the question before the discovery beat.",
            ),
            ConsistencyFindingOutput(
                severity="warning",
                type="knowledge",
                scene_index=1,
                line_id="invented-line-id",
                explanation="Rufus knows a fact that has not been established.",
                suggestion="Add an earlier reveal or remove the claim.",
            ),
            ConsistencyFindingOutput(
                severity="warning",
                type="continuity",
                scene_index=1,
                line_id="line_0001",
                explanation="The cited evidence belongs to an earlier scene.",
                suggestion="Cite a line from this scene or make this scene-level.",
            ),
            ConsistencyFindingOutput(
                severity="critical",
                type="causality",
                scene_index=999,
                line_id=None,
                explanation="This reference must be discarded.",
                suggestion="This must not reach the listener.",
            ),
        )
    )

    summary, findings = review_story_consistency(state_after_dialogue, gateway)  # type: ignore[arg-type]

    assert summary == "A concise review."
    assert len(findings) == 3
    assert findings[0].scene_id == "scene_00"
    assert findings[0].line_id == "line_0001"
    # An unknown line reference is removed while its valid scene-level warning
    # remains useful; an unknown scene makes the full finding unusable.
    assert findings[1].scene_id == "scene_01"
    assert findings[1].line_id is None
    # A known line from another scene is also discarded.  Otherwise the UI
    # would present a false evidence location for a valid scene-level finding.
    assert findings[2].scene_id == "scene_01"
    assert findings[2].line_id is None
    call = gateway.calls[0]
    assert call["kind"] == "light"
    assert "raw_text" not in call["user_content"]


def test_task_persists_sanitised_result(in_memory_session, state_after_dialogue) -> None:
    user, story, version, state = _persist_ready_story(in_memory_session, state_after_dialogue)
    check = ConsistencyCheck(
        story_id=story.id,
        version_id=version.id,
        user_id=user.id,
        status=ConsistencyCheckStatus.PENDING.value,
    )
    in_memory_session.add(check)
    in_memory_session.commit()

    gateway = ReviewGateway(
        _result(
            ConsistencyFindingOutput(
                severity="critical",
                type="knowledge",
                scene_index=1,
                line_id="line_0002",
                explanation="Rufus reveals a fact he has not learned yet.",
                suggestion="Introduce the fact before Rufus states it.",
            )
        )
    )

    @contextmanager
    def same_session():
        yield in_memory_session

    with (
        patch("daastaan_agent.tasks.session_scope", same_session),
        patch("daastaan_agent.tasks.ModelGateway", return_value=gateway),
        patch("daastaan_agent.tasks.init_tracing"),
        patch("daastaan_agent.tasks.repo.publish"),
    ):
        assert check_story_consistency.run(check.id, user.id) == check.id

    stored = in_memory_session.get(ConsistencyCheck, check.id)
    assert stored is not None
    assert stored.status == ConsistencyCheckStatus.SUCCEEDED.value
    assert stored.summary == "A concise review."
    assert stored.active_key is None
    assert stored.findings_json == [
        {
            "severity": "critical",
            "type": "knowledge",
            "scene_id": "scene_01",
            "line_id": "line_0002",
            "explanation": "Rufus reveals a fact he has not learned yet.",
            "suggestion": "Introduce the fact before Rufus states it.",
        }
    ]


def test_redelivered_task_schedules_a_non_paying_wakeup_for_an_active_lease(
    in_memory_session, state_after_dialogue
) -> None:
    user, story, version, _ = _persist_ready_story(in_memory_session, state_after_dialogue)
    active_token = "-".join(("another", "worker", "owns", "this"))
    check = ConsistencyCheck(
        story_id=story.id,
        version_id=version.id,
        user_id=user.id,
        status=ConsistencyCheckStatus.RUNNING.value,
        run_token=active_token,
        started_at=datetime.now(UTC),
    )
    in_memory_session.add(check)
    in_memory_session.commit()

    @contextmanager
    def same_session():
        yield in_memory_session

    with (
        patch("daastaan_agent.tasks.session_scope", same_session),
        patch("daastaan_agent.tasks.ModelGateway") as gateway,
        patch("daastaan_agent.tasks.init_tracing"),
        patch.object(check_story_consistency, "apply_async") as wakeup,
    ):
        assert check_story_consistency.run(check.id, user.id) == check.id

    gateway.assert_not_called()
    wakeup.assert_called_once()
    wakeup_kwargs = wakeup.call_args.kwargs
    assert wakeup_kwargs["kwargs"] == {"check_id": check.id, "user_id": user.id}
    assert wakeup_kwargs["queue"] == Queue.AGENTS.value
    assert 20 * 60 <= wakeup_kwargs["countdown"] <= 20 * 60 + 2
    stored = in_memory_session.get(ConsistencyCheck, check.id)
    assert stored is not None
    assert stored.status == ConsistencyCheckStatus.RUNNING.value
    assert stored.run_token == active_token


def test_stale_lease_is_reclaimed_and_released_after_completion(
    in_memory_session, state_after_dialogue
) -> None:
    user, story, version, _ = _persist_ready_story(in_memory_session, state_after_dialogue)
    old_token = "-".join(("lost", "worker", "run"))
    check = ConsistencyCheck(
        story_id=story.id,
        version_id=version.id,
        user_id=user.id,
        status=ConsistencyCheckStatus.RUNNING.value,
        run_token=old_token,
        started_at=datetime.now(UTC) - timedelta(minutes=21),
        active_key=version.id,
    )
    in_memory_session.add(check)
    in_memory_session.commit()
    gateway = ReviewGateway(_result())

    @contextmanager
    def same_session():
        yield in_memory_session

    with (
        patch("daastaan_agent.tasks.session_scope", same_session),
        patch("daastaan_agent.tasks.ModelGateway", return_value=gateway),
        patch("daastaan_agent.tasks.init_tracing"),
        patch("daastaan_agent.tasks.repo.publish"),
    ):
        assert check_story_consistency.run(check.id, user.id) == check.id

    stored = in_memory_session.get(ConsistencyCheck, check.id)
    assert stored is not None
    assert stored.status == ConsistencyCheckStatus.SUCCEEDED.value
    assert stored.run_token != old_token
    assert stored.active_key is None
    assert gateway.calls


def test_running_row_without_a_lease_timestamp_is_reclaimed(
    in_memory_session, state_after_dialogue
) -> None:
    user, story, version, _ = _persist_ready_story(in_memory_session, state_after_dialogue)
    abandoned_token = "-".join(("incomplete", "lease", "write"))
    check = ConsistencyCheck(
        story_id=story.id,
        version_id=version.id,
        user_id=user.id,
        status=ConsistencyCheckStatus.RUNNING.value,
        run_token=abandoned_token,
        active_key=version.id,
    )
    in_memory_session.add(check)
    in_memory_session.commit()
    gateway = ReviewGateway(_result())

    @contextmanager
    def same_session():
        yield in_memory_session

    with (
        patch("daastaan_agent.tasks.session_scope", same_session),
        patch("daastaan_agent.tasks.ModelGateway", return_value=gateway),
        patch("daastaan_agent.tasks.init_tracing"),
        patch("daastaan_agent.tasks.repo.publish"),
    ):
        assert check_story_consistency.run(check.id, user.id) == check.id

    stored = in_memory_session.get(ConsistencyCheck, check.id)
    assert stored is not None
    assert stored.status == ConsistencyCheckStatus.SUCCEEDED.value
    assert stored.run_token != abandoned_token
    assert gateway.calls


def test_api_returns_only_current_version_and_deduplicates_active_check(
    in_memory_session, state_after_dialogue
) -> None:
    user, story, current, state = _persist_ready_story(in_memory_session, state_after_dialogue)
    old_state = state.model_copy(deep=True)
    old_state.version_id = "older-version"
    old = StoryVersion(
        id=old_state.version_id,
        story_id=story.id,
        parent_version_id=None,
        version_number=0,
        state_json=old_state.model_dump(mode="json"),
    )
    old_check = ConsistencyCheck(
        story_id=story.id,
        version_id=old.id,
        user_id=user.id,
        status=ConsistencyCheckStatus.SUCCEEDED.value,
        summary="Old branch result",
    )
    current_check = ConsistencyCheck(
        story_id=story.id,
        version_id=current.id,
        user_id=user.id,
        status=ConsistencyCheckStatus.SUCCEEDED.value,
        summary="Current branch result",
    )
    in_memory_session.add(old)
    in_memory_session.add(old_check)
    in_memory_session.add(current_check)
    in_memory_session.commit()

    listed = list_consistency_checks(story, in_memory_session)
    assert [item.id for item in listed] == [current_check.id]
    assert listed[0].summary == "Current branch result"

    # A new click creates one durable pending record, and a second click returns
    # that same record without dispatching a second paid model call.
    with patch(
        "daastaan_api.routers.consistency.dispatch_consistency_check",
        return_value="celery-check-1",
    ) as dispatch:
        first = create_consistency_check(story, in_memory_session, user)
        second = create_consistency_check(story, in_memory_session, user)

    assert first.id == second.id
    assert first.status == ConsistencyCheckStatus.PENDING
    assert first.task_id == "celery-check-1"
    assert dispatch.call_count == 1
    rows = list(
        in_memory_session.exec(
            select(ConsistencyCheck).where(ConsistencyCheck.version_id == current.id)
        ).all()
    )
    assert len(rows) == 2  # prior finished result + one new in-flight result


def test_database_permits_only_one_active_check_per_version(
    in_memory_session, state_after_dialogue
) -> None:
    user, story, version, _ = _persist_ready_story(in_memory_session, state_after_dialogue)
    first = ConsistencyCheck(
        story_id=story.id,
        version_id=version.id,
        user_id=user.id,
        active_key=version.id,
    )
    duplicate = ConsistencyCheck(
        story_id=story.id,
        version_id=version.id,
        user_id=user.id,
        active_key=version.id,
    )
    in_memory_session.add(first)
    in_memory_session.add(duplicate)

    with pytest.raises(IntegrityError):
        in_memory_session.commit()

    in_memory_session.rollback()
