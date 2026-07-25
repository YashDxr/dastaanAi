from daastaan_contracts import StoryState

from daastaan_api.routers.mysteries import public_case


def _state(*, revealed: bool = False) -> StoryState:
    return StoryState(
        story_id="story", version_id="version", user_id="user", raw_text="A fair case premise.",
        content_type="mystery",
        mystery={
            "id": "case", "title": "The Quiet Bell", "premise": "A fair case.",
            "setting": "The observatory", "victim": "Dr Vale", "difficulty": "medium",
            "tone": "noir", "duration_minutes": 20,
            "suspects": [{"id": "suspect-1", "name": "Mara", "role": "curator", "personality": "calm", "public_alibi": "In the gallery", "secret": "private", "motive": "hidden", "relationship_to_victim": "colleague", "is_culprit": True}],
            "culprit_id": "suspect-1", "culprit_motive": "hidden motive",
            "clues": [{"id": "clue-1", "title": "The bell", "description": "A hidden detail", "source_location": "tower", "supports_or_contradicts": "supports", "discovery_requirement": "Search the tower", "spoiler_level": 2}],
            "red_herrings": ["The broken watch"], "crime_timeline": [{"time": "21:00", "event": "The bell rang"}],
            "solution": "Mara moved the bell.", "reveal_scene": "The lights return.", "initial_scene": "The bell is silent.",
        },
        mystery_play={"revealed": revealed, "discovered_clue_ids": [], "interrogations": [], "accusations": []},
    )


def test_public_case_redacts_solution_and_private_suspect_data() -> None:
    payload = public_case(_state())
    assert "culprit_id" not in payload
    assert "solution" not in payload
    assert payload["suspects"][0].get("secret") is None
    assert payload["suspects"][0].get("motive") is None
    assert payload["clues"][0]["locked"] is True


def test_public_case_reveals_solution_only_after_explicit_reveal() -> None:
    payload = public_case(_state(revealed=True))
    assert payload["culprit_id"] == "suspect-1"
    assert payload["solution"] == "Mara moved the bell."
    assert payload["clues"][0]["description"] == "A hidden detail"
