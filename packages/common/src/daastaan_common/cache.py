"""Content-addressed cache for paid model calls.

Two users writing the same prompt, or one user regenerating a story without
actually changing the part being regenerated, should not be billed twice. The
key is a hash of everything that determines the output - model, parameters and
the full prompt text - so a hit is only ever returned for a request that would
have produced the same result anyway.

The scope is global. Keys contain no user or story identifier, which is what
makes the saving worth having, and is safe precisely because the key covers the
entire input: a hit means the requester supplied byte-identical content.

Text results live in Redis. Audio and image bytes live in the object store under
a content-addressed key with Redis holding only the pointer, so a Redis flush
costs a re-probe of an object that is already paid for rather than a re-purchase.

Nothing here is allowed to break a call. Redis being down means a cache miss, so
the pipeline degrades to its uncached cost rather than failing.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import redis
import structlog

from .settings import get_settings
from .storage import get_store

log = structlog.get_logger(__name__)

_PREFIX = "daastaan:cache"

# Bytes are keyed by their own hash, so two identical requests share one object
# and re-running a story never grows the store.
TTS_OBJECT_PREFIX = "cache/tts"
IMAGE_OBJECT_PREFIX = "cache/image"

_redis: redis.Redis | None = None


def _client() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def digest(*parts: Any) -> str:
    """Stable hash over the full set of inputs that determine an output.

    Parts are joined with a separator that cannot appear inside them once they
    are JSON-encoded, so ("ab", "c") and ("a", "bc") cannot collide.
    """
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(json.dumps(part, sort_keys=True, default=str).encode())
        hasher.update(b"\x00")
    return hasher.hexdigest()


@dataclass(frozen=True)
class BlobHit:
    """A cached binary result: where the bytes live and what we know about them."""

    object_key: str
    meta: dict[str, Any]


def enabled() -> bool:
    return get_settings().cache_enabled


def _get_json(namespace: str, key: str) -> dict[str, Any] | None:
    if not enabled():
        return None
    try:
        raw = _client().get(f"{_PREFIX}:{namespace}:{key}")
    except Exception:
        log.warning("cache_read_failed", namespace=namespace, exc_info=True)
        return None
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("cache_value_corrupt", namespace=namespace, key=key)
        return None
    return value if isinstance(value, dict) else None


def _put_json(namespace: str, key: str, value: dict[str, Any]) -> None:
    if not enabled():
        return
    try:
        _client().set(
            f"{_PREFIX}:{namespace}:{key}",
            json.dumps(value),
            ex=get_settings().cache_ttl_seconds,
        )
    except Exception:
        log.warning("cache_write_failed", namespace=namespace, exc_info=True)


# --- structured completions -------------------------------------------------


def get_llm(key: str) -> dict[str, Any] | None:
    hit = _get_json("llm", key)
    if hit is not None:
        log.info("cache_hit", namespace="llm", key=key[:12])
    return hit


def put_llm(key: str, payload: dict[str, Any]) -> None:
    _put_json("llm", key, payload)


# --- binary results ---------------------------------------------------------


def _get_blob(namespace: str, key: str) -> BlobHit | None:
    entry = _get_json(namespace, key)
    if entry is None:
        return None

    object_key = entry.get("object_key")
    if not isinstance(object_key, str):
        return None

    # The pointer outliving the object would hand the caller a key that 404s
    # later, which is worse than paying for the call again.
    try:
        if not get_store().exists(object_key):
            log.info("cache_pointer_stale", namespace=namespace, object_key=object_key)
            return None
    except Exception:
        log.warning("cache_store_check_failed", namespace=namespace, exc_info=True)
        return None

    log.info("cache_hit", namespace=namespace, key=key[:12])
    return BlobHit(object_key=object_key, meta=entry)


def _put_blob(
    namespace: str,
    key: str,
    *,
    object_prefix: str,
    suffix: str,
    data: bytes,
    content_type: str,
    meta: dict[str, Any],
) -> str | None:
    if not enabled():
        return None
    object_key = f"{object_prefix}/{key}{suffix}"
    try:
        store = get_store()
        if not store.exists(object_key):
            store.put(object_key, data, content_type)
    except Exception:
        log.warning("cache_store_write_failed", namespace=namespace, exc_info=True)
        return None

    _put_json(namespace, key, {**meta, "object_key": object_key})
    return object_key


def get_tts(key: str) -> BlobHit | None:
    return _get_blob("tts", key)


def put_tts(key: str, *, data: bytes, duration_ms: int) -> None:
    _put_blob(
        "tts",
        key,
        object_prefix=TTS_OBJECT_PREFIX,
        suffix=".mp3",
        data=data,
        content_type="audio/mpeg",
        meta={"duration_ms": duration_ms},
    )


def get_image(key: str) -> BlobHit | None:
    return _get_blob("image", key)


def put_image(key: str, *, data: bytes) -> None:
    _put_blob(
        "image",
        key,
        object_prefix=IMAGE_OBJECT_PREFIX,
        suffix=".png",
        data=data,
        content_type="image/png",
        meta={},
    )


def read_blob(hit: BlobHit) -> bytes | None:
    """Fetch the cached bytes. A read failure is treated as a miss."""
    try:
        return get_store().get(hit.object_key)
    except Exception:
        log.warning("cache_blob_read_failed", object_key=hit.object_key, exc_info=True)
        return None
