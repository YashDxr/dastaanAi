"""Cache key discipline.

The keys are the whole safety argument for a *global* cache: a hit may only ever
be served to a request that would have produced the same output. These tests pin
that property rather than exercising Redis.
"""

from daastaan_common import cache


class TestDigest:
    def test_stable_across_calls(self) -> None:
        assert cache.digest("model", 0.2, "system") == cache.digest("model", 0.2, "system")

    def test_part_boundaries_cannot_be_shifted(self) -> None:
        # Without a separator, ("ab", "c") and ("a", "bc") would hash identically
        # and two different prompts could share one cache entry.
        assert cache.digest("ab", "c") != cache.digest("a", "bc")

    def test_temperature_changes_the_key(self) -> None:
        assert cache.digest("m", 0.0, "s") != cache.digest("m", 0.7, "s")

    def test_dict_ordering_does_not_change_the_key(self) -> None:
        # Schemas are dicts; their key order is not meaningful and must not
        # invalidate otherwise-identical entries.
        assert cache.digest({"a": 1, "b": 2}) == cache.digest({"b": 2, "a": 1})

    def test_nested_schema_difference_changes_the_key(self) -> None:
        assert cache.digest({"a": {"x": 1}}) != cache.digest({"a": {"x": 2}})


class TestGatewayKeys:
    """The gateway's own key construction, checked at the shape it uses."""

    def test_tts_key_varies_with_every_input(self) -> None:
        base = ("tts", "gpt-4o-mini-tts", "alloy", "hello", "gently")
        keys = {
            cache.digest(*base),
            cache.digest("tts", "other-model", "alloy", "hello", "gently"),
            cache.digest("tts", "gpt-4o-mini-tts", "echo", "hello", "gently"),
            cache.digest("tts", "gpt-4o-mini-tts", "alloy", "goodbye", "gently"),
            cache.digest("tts", "gpt-4o-mini-tts", "alloy", "hello", "urgently"),
        }
        assert len(keys) == 5

    def test_namespaces_do_not_collide(self) -> None:
        assert cache.digest("llm", "m", "p") != cache.digest("tts", "m", "p")
