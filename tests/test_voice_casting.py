"""Casting tests.

The bug these guard against is a cast that sounds like two people: the previous
implementation mapped narrative role to one of four voices and rotated supporting
characters through five, so a six-character story routinely reused timbres and
relied on delivery instructions to tell them apart.
"""

import pytest
from daastaan_agent import casting
from daastaan_contracts import Character, CharacterRole, VoiceAge, VoiceGender, limits


def character(
    index: int,
    role: CharacterRole,
    gender: VoiceGender = VoiceGender.NEUTRAL,
    age: VoiceAge = VoiceAge.ADULT,
    name: str | None = None,
) -> Character:
    return Character(
        id=f"char_{index:04d}",
        name=name or f"Character {index}",
        role=role,
        personality="Terse and watchful.",
        sample_line="We should go.",
        gender=gender,
        age=age,
    )


def full_cast() -> list[Character]:
    """Six characters, the maximum a story may hold."""
    return [
        character(0, CharacterRole.NARRATOR, VoiceGender.NEUTRAL, VoiceAge.ELDER),
        character(1, CharacterRole.PROTAGONIST, VoiceGender.FEMININE, VoiceAge.YOUNG),
        character(2, CharacterRole.ANTAGONIST, VoiceGender.MASCULINE, VoiceAge.ELDER),
        character(3, CharacterRole.SUPPORTING, VoiceGender.MASCULINE, VoiceAge.YOUNG),
        character(4, CharacterRole.SUPPORTING, VoiceGender.FEMININE, VoiceAge.ADULT),
        character(5, CharacterRole.SUPPORTING, VoiceGender.MASCULINE, VoiceAge.ADULT),
    ]


class TestCatalogue:
    def test_enough_voices_for_the_largest_cast(self) -> None:
        assert len(casting.VOICE_CATALOGUE) >= limits.MAX_CHARACTERS

    def test_voice_ids_are_unique(self) -> None:
        ids = [voice.id for voice in casting.VOICE_CATALOGUE]
        assert len(set(ids)) == len(ids)

    def test_every_gender_is_represented(self) -> None:
        genders = {voice.gender for voice in casting.VOICE_CATALOGUE}
        assert genders == set(VoiceGender)


class TestUniqueness:
    def test_no_two_characters_share_a_voice(self) -> None:
        cast = full_cast()
        assigned = casting.assign_voices(cast)

        voices = [assigned[c.id].id for c in cast]
        assert len(set(voices)) == len(cast)

    def test_every_character_is_cast(self) -> None:
        cast = full_cast()
        assigned = casting.assign_voices(cast)
        assert set(assigned) == {c.id for c in cast}

    def test_all_supporting_cast_still_differs(self) -> None:
        """The old rotation was the direct cause of repeated voices, so a cast
        made entirely of supporting characters is the interesting case."""
        cast = [character(i, CharacterRole.SUPPORTING) for i in range(limits.MAX_CHARACTERS)]
        assigned = casting.assign_voices(cast)
        assert len({assigned[c.id].id for c in cast}) == limits.MAX_CHARACTERS

    def test_empty_cast_is_not_an_error(self) -> None:
        assert casting.assign_voices([]) == {}


class TestGenderMatching:
    @pytest.mark.parametrize(
        "gender", [VoiceGender.FEMININE, VoiceGender.MASCULINE]
    )
    def test_a_lone_character_gets_a_matching_voice(self, gender: VoiceGender) -> None:
        cast = [character(0, CharacterRole.PROTAGONIST, gender)]
        assigned = casting.assign_voices(cast)
        assert assigned["char_0000"].gender == gender

    def test_gender_outranks_age(self) -> None:
        """A feminine elder should be cast feminine rather than given a masculine
        voice that happens to be the right age."""
        cast = [character(0, CharacterRole.PROTAGONIST, VoiceGender.FEMININE, VoiceAge.ELDER)]
        assert casting.assign_voices(cast)["char_0000"].gender == VoiceGender.FEMININE

    def test_full_cast_is_gender_matched_where_possible(self) -> None:
        cast = full_cast()
        assigned = casting.assign_voices(cast)
        for member in cast:
            voice = assigned[member.id]
            # A neutral on either side is an acceptable compromise; an outright
            # inversion is not.
            assert VoiceGender.NEUTRAL in (member.gender, voice.gender) or (
                member.gender == voice.gender
            ), f"{member.name} ({member.gender}) was cast as {voice.id} ({voice.gender})"


class TestDeterminism:
    def test_repeated_calls_agree(self) -> None:
        cast = full_cast()
        first = {k: v.id for k, v in casting.assign_voices(cast).items()}
        second = {k: v.id for k, v in casting.assign_voices(full_cast()).items()}
        assert first == second

    def test_input_order_does_not_change_a_character_s_voice(self) -> None:
        """Leads are cast in role order, so reordering the list must not recast
        them - otherwise a regeneration could silently swap voices."""
        cast = full_cast()
        baseline = {k: v.id for k, v in casting.assign_voices(cast).items()}
        shuffled = {
            k: v.id for k, v in casting.assign_voices(list(reversed(cast))).items()
        }
        assert baseline == shuffled


class TestPins:
    def test_a_pinned_role_gets_the_pinned_voice(self) -> None:
        assigned = casting.assign_voices(full_cast(), pins={"narrator": "onyx"})
        assert assigned["char_0000"].id == "onyx"

    def test_a_pinned_voice_is_withdrawn_from_the_pool(self) -> None:
        cast = full_cast()
        assigned = casting.assign_voices(cast, pins={"narrator": "onyx"})
        others = [assigned[c.id].id for c in cast if c.id != "char_0000"]
        assert "onyx" not in others
        assert len(set(others)) == len(others)

    def test_a_pin_applies_to_one_character_not_the_whole_role(self) -> None:
        """The old presets dictionary gave every supporting character the same
        voice, which is the behaviour being replaced."""
        cast = full_cast()
        assigned = casting.assign_voices(cast, pins={"supporting": "coral"})
        supporting = [assigned[c.id].id for c in cast if c.role is CharacterRole.SUPPORTING]
        assert supporting.count("coral") == 1
        assert len(set(supporting)) == len(supporting)

    def test_an_unknown_pin_is_ignored_rather_than_fatal(self) -> None:
        cast = full_cast()
        assigned = casting.assign_voices(cast, pins={"narrator": "not-a-voice"})
        assert assigned["char_0000"].id in casting.VOICES_BY_ID
        assert len({assigned[c.id].id for c in cast}) == len(cast)


class TestInstructions:
    def test_the_narrator_uses_the_persona_template(self) -> None:
        narrator = character(0, CharacterRole.NARRATOR)
        voice = casting.VOICES_BY_ID["fable"]
        assert casting.describe(narrator, voice, "Speak as a village elder.") == (
            "Speak as a village elder."
        )

    def test_a_character_description_carries_name_and_timbre(self) -> None:
        member = character(1, CharacterRole.PROTAGONIST, name="Mira")
        voice = casting.VOICES_BY_ID["nova"]
        described = casting.describe(member, voice, None)
        assert "Mira" in described
        assert voice.timbre in described

    def test_a_non_adult_age_is_stated(self) -> None:
        member = character(2, CharacterRole.SUPPORTING, age=VoiceAge.CHILD, name="Pip")
        described = casting.describe(member, casting.VOICES_BY_ID["shimmer"], None)
        assert "child" in described
