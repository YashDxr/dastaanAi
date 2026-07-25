"""Object storage behind one interface.

`UCVolumeStore` is the Databricks path and `LocalFsStore` is the fallback, so the
demo can run with or without a Databricks account by flipping `storage_backend`.

Every key is validated before it touches a filesystem or a volume path. Keys are
minted from UUIDs by `ids.py` and are never derived from model output - this is
the concrete form of the rule that generated text must never become a path.
"""

import re
from io import BytesIO
from pathlib import Path
from typing import Protocol

import structlog

from .settings import get_settings

log = structlog.get_logger(__name__)

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_.-]{0,200}$")


class UnsafeObjectKeyError(ValueError):
    pass


def validate_key(key: str) -> str:
    if not _SAFE_KEY.match(key) or ".." in key:
        raise UnsafeObjectKeyError(f"refusing unsafe object key: {key!r}")
    return key


class MediaStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


class LocalFsStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / validate_key(key)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class UCVolumeStore:
    """Unity Catalog Volumes via the Databricks SDK.

    Works from anywhere with workspace credentials, so the worker does not need
    to run on Databricks.
    """

    def __init__(self, volume_path: str) -> None:
        from databricks.sdk import WorkspaceClient

        self.volume_path = volume_path.rstrip("/")
        self.client = WorkspaceClient()

    def _path(self, key: str) -> str:
        return f"{self.volume_path}/{validate_key(key)}"

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.client.files.upload(self._path(key), BytesIO(data), overwrite=True)
        return key

    def get(self, key: str) -> bytes:
        response = self.client.files.download(self._path(key))
        return response.contents.read()

    def exists(self, key: str) -> bool:
        try:
            self.client.files.get_metadata(self._path(key))
        except Exception:
            return False
        return True


_store: MediaStore | None = None


def get_store() -> MediaStore:
    global _store
    if _store is not None:
        return _store

    settings = get_settings()
    if settings.storage_backend == "uc_volume":
        if not settings.uc_volume_path:
            raise ValueError("storage_backend=uc_volume requires uc_volume_path")
        _store = UCVolumeStore(settings.uc_volume_path)
        log.info("storage_ready", backend="uc_volume", path=settings.uc_volume_path)
    else:
        _store = LocalFsStore(settings.local_media_dir)
        log.info("storage_ready", backend="local", path=settings.local_media_dir)
    return _store
