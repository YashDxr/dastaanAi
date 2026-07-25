"""Identifier and object-key minting.

All entity ids and storage keys originate here so that nothing derived from model
output can reach a path, a filename, or a shell argument. Stages that need to
reference an entity echo one of these ids back, and we reject unknown values.
"""

import uuid

from daastaan_contracts import AssetKind


def new_id() -> str:
    return uuid.uuid4().hex


def scene_id(index: int) -> str:
    return f"scene_{index:02d}"


def character_id(index: int) -> str:
    return f"char_{index:02d}"


def line_id(index: int) -> str:
    return f"line_{index:04d}"


def dedupe_key(
    kind: AssetKind,
    *,
    line_id: str | None = None,
    scene_id: str | None = None,
    tag: str | None = None,
) -> str:
    """Stable per-(version, artifact) key used for the idempotency guard."""
    suffix = line_id or scene_id or "single"
    if tag:
        suffix = f"{suffix}:{tag}"
    return f"{kind.value}:{suffix}"


def object_key(
    version_id: str,
    kind: AssetKind,
    *,
    line_id: str | None = None,
    scene_id: str | None = None,
    tag: str | None = None,
    ext: str = "mp3",
) -> str:
    suffix = line_id or scene_id or "final"
    if tag:
        suffix = f"{suffix}_{tag}"
    return f"{version_id}/{kind.value}/{suffix}.{ext}"
