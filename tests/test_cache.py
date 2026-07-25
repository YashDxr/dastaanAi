"""Cache key discipline.

The keys are the whole safety argument for a *global* cache: a hit may only ever
be served to a request that would have produced the same output. These tests pin
that property rather than exercising Redis.
"""

from daastaan_common import cache
from daastaan_common.settings import Settings


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


class TestCacheDatabase:
    """The cache lives apart from the Celery broker, which is what makes it
    findable in the Redis UI."""

    def settings(self, redis_url: str) -> Settings:
        return Settings(redis_url=redis_url, jwt_secret="x" * 40)

    def test_the_cache_url_points_at_the_cache_db(self) -> None:
        assert self.settings("redis://redis:6379/0").cache_redis_url() == "redis://redis:6379/1"

    def test_the_host_follows_redis_url(self) -> None:
        """Derived rather than configured separately, so moving Redis moves the
        cache with it instead of leaving it writing to the old host."""
        url = self.settings("redis://elsewhere:6380/0").cache_redis_url()
        assert url == "redis://elsewhere:6380/1"

    def test_credentials_and_scheme_survive(self) -> None:
        url = self.settings("rediss://user:pw@example.com:6380/3").cache_redis_url()
        assert url == "rediss://user:pw@example.com:6380/1"

    def test_the_cache_db_is_not_the_broker_db(self) -> None:
        settings = self.settings("redis://redis:6379/0")
        assert settings.cache_redis_url() != settings.redis_url


class TestProvenance:
    """Metadata that makes a SHA-256 key mean something to a person reading the
    Redis UI. None of it may reach the key itself, which would defeat a global
    content-addressed cache."""

    def source(self, **kwargs) -> cache.Source:  # type: ignore[no-untyped-def]
        return cache.Source(
            stage="mood_classification",
            model="gpt-4o",
            story_id="story-1",
            version_id="ver-1",
            text="A lighthouse keeper who has not slept in nine days.",
            **kwargs,
        )

    def test_metadata_keys_are_underscore_prefixed(self) -> None:
        """The prefix is how `get_llm` tells bookkeeping from model output, so a
        payload field named `stage` cannot be mistaken for ours."""
        assert all(key.startswith("_") for key in self.source().as_meta())

    def test_metadata_records_where_the_entry_came_from(self) -> None:
        meta = self.source().as_meta()
        assert meta["_stage"] == "mood_classification"
        assert meta["_model"] == "gpt-4o"
        assert meta["_story_id"] == "story-1"
        assert meta["_version_id"] == "ver-1"

    def test_the_preview_is_truncated(self) -> None:
        long = cache.Source(stage="s", model="m", text="word " * 500).as_meta()["_preview"]
        assert len(long) <= cache.PREVIEW_CHARS + 1

    def test_the_preview_collapses_whitespace(self) -> None:
        assert cache.preview("a\n\n  b\tc") == "a b c"

    def test_short_text_is_not_marked_as_truncated(self) -> None:
        assert cache.preview("short") == "short"
