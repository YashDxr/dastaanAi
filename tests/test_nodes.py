"""Isolated tests for each reasoning node.

Each node is ``(session, state, gateway) -> StoryState`` - pure function
signature with no database or network dependencies when the gateway is mocked.
"""

from daastaan_agent import casting
from daastaan_agent.nodes import (
    character_registry,
    dialogue_attribution,
    emotion_tagging,
    mood_classification,
    narrator_persona,
    voice_assignment,
)
from daastaan_contracts import (
    CharacterRole,
    LineType,
    Scope,
    StageName,
    ValidatedDirective,
    VoiceGender,
)


class TestMoodClassification:
    def test_sets_mood(self, stub_session, sample_state, mock_gateway):
        result = mood_classification(stub_session, sample_state, mock_gateway)
        assert result.mood is not None
        assert result.mood.genre == "fantasy"
        assert result.mood.mood == "whimsical"

    def test_calls_moderate_before_structured(
        self, stub_session, sample_state, mock_gateway
    ):
        mood_classification(stub_session, sample_state, mock_gateway)
        assert mock_gateway.calls[0]["method"] == "moderate"
        assert mock_gateway.calls[1]["method"] == "structured"


class TestStoryUnderstanding:
    def test_populates_scenes_and_title(
        self, stub_session, state_after_mood, mock_gateway
    ):
        from daastaan_agent.nodes import story_understanding

        result = story_understanding(stub_session, state_after_mood, mock_gateway)
        assert result.title == "The Enchanted Garden"
        assert len(result.scenes) == 2
        assert result.scenes[0].id == "scene_00"
        assert result.scenes[1].id == "scene_01"
        assert result.arc_summary is not None
        assert result.setting is not None

    def test_scene_branch_includes_the_current_timeline_for_continuity(
        self, stub_session, state_after_mood, mock_gateway
    ):
        """A second alternate future must branch from the current version, not
        silently return to the original raw text's timeline."""
        # The fixture's canned scenes are normally installed by story
        # understanding itself, so make the persisted branch explicit here.
        from daastaan_contracts import Scene

        state_after_mood.scenes = [
            Scene(
                id="scene_00",
                index=0,
                title="Discovery",
                summary="Lily finds the secret door.",
                setting="Garden wall",
                mood_tag="mysterious",
            ),
            Scene(
                id="scene_01",
                index=1,
                title="New Choice",
                summary="Lily leaves the garden to seek help.",
                setting="Village road",
                mood_tag="urgent",
            ),
        ]
        state_after_mood.regen = ValidatedDirective(
            scope=Scope.SCENE,
            target_stage=StageName.STORY_UNDERSTANDING,
            target_id="scene_01",
            instruction_delta="Lily asks the village for help instead.",
        )

        captured: dict[str, str] = {}
        original_structured = mock_gateway.structured

        def capture_request(**kwargs):
            captured["user_content"] = kwargs["user_content"]
            return original_structured(**kwargs)

        mock_gateway.structured = capture_request

        from daastaan_agent.nodes import story_understanding

        story_understanding(stub_session, state_after_mood, mock_gateway)
        request = captured["user_content"]

        assert "<current_timeline>" in request
        assert "Lily leaves the garden to seek help." in request
        assert "Preserve every event, fact, character motivation" in request
        assert (
            "Additional direction from the listener: Lily asks the village for help instead."
            in request
        )


class TestCharacterRegistry:
    def test_populates_characters(
        self, stub_session, state_after_mood, mock_gateway
    ):
        result = character_registry(stub_session, state_after_mood, mock_gateway)
        assert len(result.characters) >= 2
        names = {c.name for c in result.characters}
        assert "Narrator" in names
        assert "Lily" in names

    def test_ensures_narrator_exists(
        self, stub_session, state_after_mood, mock_gateway
    ):
        result = character_registry(stub_session, state_after_mood, mock_gateway)
        narrators = [
            c for c in result.characters if c.role is CharacterRole.NARRATOR
        ]
        assert len(narrators) >= 1


class TestDialogueAttribution:
    def test_populates_lines(
        self, stub_session, state_after_mood, mock_gateway
    ):
        from daastaan_agent.nodes import story_understanding

        state = story_understanding(stub_session, state_after_mood, mock_gateway)
        state = character_registry(stub_session, state, mock_gateway)
        result = dialogue_attribution(stub_session, state, mock_gateway)
        assert len(result.lines) == 3
        assert all(line.id.startswith("line_") for line in result.lines)

    def test_lines_reference_valid_scenes(
        self, stub_session, state_after_mood, mock_gateway
    ):
        from daastaan_agent.nodes import story_understanding

        state = story_understanding(stub_session, state_after_mood, mock_gateway)
        state = character_registry(stub_session, state, mock_gateway)
        result = dialogue_attribution(stub_session, state, mock_gateway)
        scene_ids = {s.id for s in result.scenes}
        for line in result.lines:
            assert line.scene_id in scene_ids

    def test_narration_attributed_to_narrator(
        self, stub_session, state_after_mood, mock_gateway
    ):
        from daastaan_agent.nodes import story_understanding

        state = story_understanding(stub_session, state_after_mood, mock_gateway)
        state = character_registry(stub_session, state, mock_gateway)
        result = dialogue_attribution(stub_session, state, mock_gateway)
        narration_lines = [
            ln for ln in result.lines if ln.line_type is LineType.NARRATION
        ]
        narrator = next(
            c for c in result.characters if c.role is CharacterRole.NARRATOR
        )
        for line in narration_lines:
            assert line.character_id == narrator.id


class TestEmotionTagging:
    def test_tags_all_lines(
        self, stub_session, state_after_dialogue, mock_gateway
    ):
        result = emotion_tagging(stub_session, state_after_dialogue, mock_gateway)
        tagged = [
            ln for ln in result.lines if ln.emotion is not None
        ]
        assert len(tagged) == 3

    def test_preserves_existing_line_data(
        self, stub_session, state_after_dialogue, mock_gateway
    ):
        result = emotion_tagging(stub_session, state_after_dialogue, mock_gateway)
        assert result.lines[0].text == "The old garden wall stretched endlessly."
        assert result.lines[0].speaker == "Narrator"


class TestNarratorPersona:
    def test_sets_persona(
        self, stub_session, state_after_dialogue, mock_gateway
    ):
        result = narrator_persona(stub_session, state_after_dialogue, mock_gateway)
        assert result.narrator_persona is not None
        assert result.narrator_persona.persona_name == "The Garden Keeper"
        assert result.narrator_persona.delivery_template is not None


class TestVoiceAssignment:
    def test_assigns_voices_to_all_characters(
        self, stub_session, state_after_dialogue, mock_gateway
    ):
        state = narrator_persona(stub_session, state_after_dialogue, mock_gateway)
        result = voice_assignment(stub_session, state, mock_gateway)
        assert len(result.voice_map) == len(result.characters)
        for assignment in result.voice_map:
            assert assignment.voice_preset
            assert assignment.base_instructions

    def test_every_character_gets_a_different_voice(
        self, stub_session, state_after_dialogue, mock_gateway
    ):
        """The point of the casting stage. Voices used to be picked by narrative
        role, so a cast routinely shared timbres and differed only in delivery."""
        state = narrator_persona(stub_session, state_after_dialogue, mock_gateway)
        result = voice_assignment(stub_session, state, mock_gateway)

        voices = [a.voice_preset for a in result.voice_map]
        assert len(set(voices)) == len(result.characters)

    def test_voices_match_the_character_s_vocal_gender(
        self, stub_session, state_after_dialogue, mock_gateway
    ):
        state = narrator_persona(stub_session, state_after_dialogue, mock_gateway)
        result = voice_assignment(stub_session, state, mock_gateway)

        for character in result.characters:
            voice = casting.VOICES_BY_ID[character.voice_preset]
            assert VoiceGender.NEUTRAL in (character.gender, voice.gender) or (
                character.gender == voice.gender
            )

    def test_the_state_and_the_voice_map_agree(
        self, stub_session, state_after_dialogue, mock_gateway
    ):
        """`tts_line` reads `character.voice_preset`, not `voice_map`, so the two
        drifting apart would silently synthesise the wrong voice."""
        state = narrator_persona(stub_session, state_after_dialogue, mock_gateway)
        result = voice_assignment(stub_session, state, mock_gateway)

        by_id = {a.character_id: a for a in result.voice_map}
        for character in result.characters:
            assert by_id[character.id].voice_preset == character.voice_preset
            assert by_id[character.id].base_instructions == character.base_instructions

    def test_the_narrator_speaks_with_the_persona_template(
        self, stub_session, state_after_dialogue, mock_gateway
    ):
        state = narrator_persona(stub_session, state_after_dialogue, mock_gateway)
        result = voice_assignment(stub_session, state, mock_gateway)

        narrator = next(c for c in result.characters if c.role is CharacterRole.NARRATOR)
        assert narrator.base_instructions == state.narrator_persona.delivery_template

    def test_no_model_call(
        self, stub_session, state_after_dialogue, mock_gateway
    ):
        """Voice assignment is deterministic - no LLM call needed."""
        state = narrator_persona(stub_session, state_after_dialogue, mock_gateway)
        calls_before = len(mock_gateway.calls)
        voice_assignment(stub_session, state, mock_gateway)
        after = mock_gateway.calls[calls_before:]
        structured_calls = [
            c for c in after if c["method"] == "structured"
        ]
        assert len(structured_calls) == 0
