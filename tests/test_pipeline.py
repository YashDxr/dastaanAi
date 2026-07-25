"""Full pipeline integration test with mocked gateway.

Runs the LangGraph pipeline end-to-end in-process, verifying stage ordering,
state flow, and completed_stages tracking - all without any external services.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from daastaan_agent.graph import run_agent_stages
from daastaan_contracts import JobStatus, StageName, StoryState


def _mock_job(story_id: str) -> object:
    return type("Job", (), {"story_id": story_id, "stage": ""})()


@contextmanager
def _fake_session_scope():
    """Yield a MagicMock that acts as a session inside ``with session_scope()``."""
    yield MagicMock()


class TestFullPipeline:
    def test_runs_all_stages(
        self, in_memory_session, sample_state, mock_gateway
    ):
        """The graph should execute all 7 reasoning stages."""
        with (
            patch(
                "daastaan_agent.graph.ModelGateway",
                return_value=mock_gateway,
            ),
            patch("daastaan_agent.graph.repo") as mock_repo,
            patch(
                "daastaan_agent.graph.session_scope",
                side_effect=_fake_session_scope,
            ),
        ):
            mock_repo.start_job.return_value = _mock_job(
                sample_state.story_id
            )
            mock_repo.finish_job.return_value = None
            mock_repo.save_state.return_value = None

            result = run_agent_stages(sample_state, "test-user")

        assert isinstance(result, StoryState)
        expected_stages = {
            StageName.MOOD_CLASSIFICATION,
            StageName.STORY_UNDERSTANDING,
            StageName.CHARACTER_REGISTRY,
            StageName.DIALOGUE_ATTRIBUTION,
            StageName.EMOTION_TAGGING,
            StageName.NARRATOR_PERSONA,
            StageName.VOICE_ASSIGNMENT,
        }
        assert set(result.completed_stages) == expected_stages

    def test_produces_complete_state(
        self, in_memory_session, sample_state, mock_gateway
    ):
        """After a full run the state should have all fields populated."""
        with (
            patch(
                "daastaan_agent.graph.ModelGateway",
                return_value=mock_gateway,
            ),
            patch("daastaan_agent.graph.repo") as mock_repo,
            patch(
                "daastaan_agent.graph.session_scope",
                side_effect=_fake_session_scope,
            ),
        ):
            mock_repo.start_job.return_value = _mock_job(
                sample_state.story_id
            )
            mock_repo.finish_job.return_value = None
            mock_repo.save_state.return_value = None

            result = run_agent_stages(sample_state, "test-user")

        assert result.mood is not None
        assert result.title is not None
        assert len(result.scenes) > 0
        assert len(result.characters) > 0
        assert len(result.lines) > 0
        assert result.narrator_persona is not None
        assert len(result.voice_map) > 0

    def test_stages_execute_in_order(
        self, in_memory_session, sample_state, mock_gateway
    ):
        """Verify the linear stages run before the parallel ones, and
        voice_assignment runs last."""
        executed_stages: list[str] = []

        with (
            patch(
                "daastaan_agent.graph.ModelGateway",
                return_value=mock_gateway,
            ),
            patch("daastaan_agent.graph.repo") as mock_repo,
            patch(
                "daastaan_agent.graph.session_scope",
                side_effect=_fake_session_scope,
            ),
        ):

            def track_start_job(session, *, story_id, version_id, stage):
                executed_stages.append(stage.value)
                return _mock_job(story_id)

            mock_repo.start_job.side_effect = track_start_job
            mock_repo.finish_job.return_value = None
            mock_repo.save_state.return_value = None

            run_agent_stages(sample_state, "test-user")

        # First 4 stages should be in strict order
        assert executed_stages[:4] == [
            "mood_classification",
            "story_understanding",
            "character_registry",
            "dialogue_attribution",
        ]
        # Stages 5 and 6 can be in either order (parallel)
        parallel = set(executed_stages[4:6])
        assert parallel == {"emotion_tagging", "narrator_persona"}
        # Stage 7 is always last
        assert executed_stages[6] == "voice_assignment"

    def test_state_round_trips(
        self, in_memory_session, sample_state, mock_gateway
    ):
        """The result should serialise and deserialise cleanly."""
        with (
            patch(
                "daastaan_agent.graph.ModelGateway",
                return_value=mock_gateway,
            ),
            patch("daastaan_agent.graph.repo") as mock_repo,
            patch(
                "daastaan_agent.graph.session_scope",
                side_effect=_fake_session_scope,
            ),
        ):
            mock_repo.start_job.return_value = _mock_job(
                sample_state.story_id
            )
            mock_repo.finish_job.return_value = None
            mock_repo.save_state.return_value = None

            result = run_agent_stages(sample_state, "test-user")

        dumped = result.model_dump(mode="json")
        restored = StoryState.model_validate(dumped)
        assert restored.title == result.title
        assert len(restored.lines) == len(result.lines)
        assert len(restored.completed_stages) == len(result.completed_stages)


class TestOutputFormatFlow:
    def test_output_format_flows_through_state(
        self, in_memory_session, mock_gateway
    ):
        """output_format should survive a LangGraph round-trip."""
        from conftest import _base_state

        state = _base_state(output_format="video")
        assert state.output_format == "video"

        with (
            patch(
                "daastaan_agent.graph.ModelGateway",
                return_value=mock_gateway,
            ),
            patch("daastaan_agent.graph.repo") as mock_repo,
            patch(
                "daastaan_agent.graph.session_scope",
                side_effect=_fake_session_scope,
            ),
        ):
            mock_repo.start_job.return_value = _mock_job(state.story_id)
            mock_repo.finish_job.return_value = None
            mock_repo.save_state.return_value = None

            result = run_agent_stages(state, "test-user")

        assert result.output_format == "video"

    def test_output_format_defaults_to_audio(self):
        """Without explicit output_format, state defaults to 'audio'."""
        state = StoryState(
            story_id="s1", version_id="v1", user_id="u1",
            raw_text="x" * 30,
        )
        assert state.output_format == "audio"

        dumped = state.model_dump(mode="json")
        restored = StoryState.model_validate(dumped)
        assert restored.output_format == "audio"


class TestLanguageFlow:
    def test_language_flows_through_state(
        self, in_memory_session, mock_gateway
    ):
        """language should survive a LangGraph round-trip."""
        from conftest import _base_state

        state = _base_state(language="hi")
        assert state.language == "hi"

        with (
            patch(
                "daastaan_agent.graph.ModelGateway",
                return_value=mock_gateway,
            ),
            patch("daastaan_agent.graph.repo") as mock_repo,
            patch(
                "daastaan_agent.graph.session_scope",
                side_effect=_fake_session_scope,
            ),
        ):
            mock_repo.start_job.return_value = _mock_job(state.story_id)
            mock_repo.finish_job.return_value = None
            mock_repo.save_state.return_value = None

            result = run_agent_stages(state, "test-user")

        assert result.language == "hi"

    def test_language_defaults_to_en(self):
        """Without explicit language, state defaults to 'en'."""
        state = StoryState(
            story_id="s1", version_id="v1", user_id="u1",
            raw_text="x" * 30,
        )
        assert state.language == "en"

        dumped = state.model_dump(mode="json")
        restored = StoryState.model_validate(dumped)
        assert restored.language == "en"


class TestGraphErrorHandling:
    def test_node_failure_propagates(
        self, in_memory_session, sample_state, mock_gateway
    ):
        """If a node raises, the graph should propagate the exception."""
        mock_gateway._responses.clear()

        with (
            patch(
                "daastaan_agent.graph.ModelGateway",
                return_value=mock_gateway,
            ),
            patch("daastaan_agent.graph.repo") as mock_repo,
            patch(
                "daastaan_agent.graph.session_scope",
                side_effect=_fake_session_scope,
            ),
        ):
            mock_repo.start_job.return_value = _mock_job(
                sample_state.story_id
            )
            mock_repo.finish_job.return_value = None
            mock_repo.save_state.return_value = None

            with pytest.raises(ValueError):
                run_agent_stages(sample_state, "test-user")

    def test_failed_node_records_job_failure(
        self, in_memory_session, sample_state, mock_gateway
    ):
        """When a node fails, finish_job is called with FAILED status."""
        mock_gateway._responses.clear()

        with (
            patch(
                "daastaan_agent.graph.ModelGateway",
                return_value=mock_gateway,
            ),
            patch("daastaan_agent.graph.repo") as mock_repo,
            patch(
                "daastaan_agent.graph.session_scope",
                side_effect=_fake_session_scope,
            ),
        ):
            mock_repo.start_job.return_value = _mock_job(
                sample_state.story_id
            )
            mock_repo.finish_job.return_value = None
            mock_repo.save_state.return_value = None

            with pytest.raises(ValueError):
                run_agent_stages(sample_state, "test-user")

            fail_calls = [
                call
                for call in mock_repo.finish_job.call_args_list
                if call.kwargs.get("status") == JobStatus.FAILED
            ]
            assert len(fail_calls) >= 1
