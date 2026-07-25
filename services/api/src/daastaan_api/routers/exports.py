"""Downloading the finished episode in other formats.

The pipeline produces one master: a 128 kbps MP3. Everything offered here is a
transcode of it, which is why MP3 is the recommended download and is served
straight from the master rather than re-encoded - a second pass through a lossy
encoder would cost quality and buy nothing.

Transcoding is a worker job, not request work, so this endpoint either returns
an asset that already exists or accepts the request and lets the client poll.
"""

import structlog
from daastaan_common.models import MediaAsset, StoryVersion
from daastaan_contracts import AssetKind
from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from ..deps import CurrentUser, OwnedStory, SessionDep
from ..dispatch import dispatch_audio_export, dispatch_bgm_export
from ..schemas import ExportFormatOut, ExportOut, ExportRequest

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/stories", tags=["exports"])

# Kept in step with `daastaan_agent.assembly.AUDIO_EXPORTS`, plus the master. The
# API cannot import the agent package - that is the whole point of dispatching by
# task name - so the list is duplicated and the worker rejects anything it does
# not recognise.
FORMATS: dict[str, dict[str, str]] = {
    "mp3": {
        "label": "MP3",
        "content_type": "audio/mpeg",
        "detail": "The original mix, not re-encoded. Plays everywhere.",
    },
    "m4a": {
        "label": "M4A (AAC)",
        "content_type": "audio/mp4",
        "detail": "Best for Apple devices, iMovie and Final Cut.",
    },
    "opus": {
        "label": "Opus",
        "content_type": "audio/ogg",
        "detail": "Smallest file at the same quality. Not read by older Apple software.",
    },
    "flac": {
        "label": "FLAC",
        "content_type": "audio/flac",
        "detail": "Lossless container around a 128 kbps source: larger, no quality gain.",
    },
    "wav": {
        "label": "WAV",
        "content_type": "audio/wav",
        "detail": "Uncompressed, for editing. Very large, no quality gain.",
    },
}

RECOMMENDED = "mp3"


def _current_version(story, session) -> StoryVersion:  # type: ignore[no-untyped-def]
    version = (
        session.get(StoryVersion, story.current_version_id)
        if story.current_version_id
        else None
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this story has no finished episode yet")
    return version


def _assets(session, version_id: str) -> dict[str, MediaAsset]:  # type: ignore[no-untyped-def]
    """Available downloads by format.

    The master answers for `mp3`; every other entry is a stored transcode, keyed
    by the format suffix of its dedupe key.
    """
    rows = session.exec(
        select(MediaAsset).where(
            MediaAsset.version_id == version_id,
            MediaAsset.kind.in_([AssetKind.FINAL_EPISODE, AssetKind.EPISODE_EXPORT]),
        )
    ).all()

    found: dict[str, MediaAsset] = {}
    for asset in rows:
        if asset.kind == AssetKind.FINAL_EPISODE:
            found["mp3"] = asset
        else:
            found[asset.dedupe_key.rpartition(":")[2]] = asset
    return found


def _listing(session, version_id: str) -> list[ExportFormatOut]:  # type: ignore[no-untyped-def]
    available = _assets(session, version_id)
    return [
        ExportFormatOut(
            format=fmt,
            label=meta["label"],
            content_type=meta["content_type"],
            detail=meta["detail"],
            recommended=fmt == RECOMMENDED,
            ready=fmt in available,
            url=f"/api/media/{available[fmt].id}?download=1" if fmt in available else None,
            size_bytes=available[fmt].size_bytes if fmt in available else None,
        )
        for fmt, meta in FORMATS.items()
    ]


@router.get("/{story_id}/exports", response_model=list[ExportFormatOut])
def list_exports(story: OwnedStory, session: SessionDep) -> list[ExportFormatOut]:
    version = _current_version(story, session)
    return _listing(session, version.id)


@router.post("/{story_id}/exports", response_model=ExportOut)
def create_export(
    story: OwnedStory, body: ExportRequest, session: SessionDep, user: CurrentUser
) -> ExportOut:
    if body.format not in FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown format: {body.format}")

    version = _current_version(story, session)
    available = _assets(session, version.id)

    if "mp3" not in available:
        raise HTTPException(status.HTTP_409_CONFLICT, "this story has no finished episode yet")

    if asset := available.get(body.format):
        return ExportOut(
            format=body.format,
            ready=True,
            url=f"/api/media/{asset.id}?download=1",
            size_bytes=asset.size_bytes,
        )

    dispatch_audio_export(version_id=version.id, user_id=user.id, fmt=body.format)
    return ExportOut(format=body.format, ready=False, url=None, size_bytes=None)


# ---------------------------------------------------------------------------
# BGM (background music bed) exports
# ---------------------------------------------------------------------------

BGM_FORMATS: dict[str, dict[str, str]] = {
    "wav": {
        "label": "WAV",
        "content_type": "audio/wav",
        "detail": "Original lossless file from Stable Audio. Best quality, largest file.",
    },
    "mp3": {
        "label": "MP3",
        "content_type": "audio/mpeg",
        "detail": "First-generation encode from lossless source. Great quality, plays everywhere.",
    },
    "flac": {
        "label": "FLAC",
        "content_type": "audio/flac",
        "detail": "Lossless container. Same quality as WAV, slightly smaller.",
    },
    "m4a": {
        "label": "M4A (AAC)",
        "content_type": "audio/mp4",
        "detail": "Best for Apple devices and video editing on Mac.",
    },
    "opus": {
        "label": "Opus",
        "content_type": "audio/ogg",
        "detail": "Smallest file. Not supported by older Apple software.",
    },
}

BGM_RECOMMENDED = "wav"


def _bgm_assets(session, version_id: str) -> dict[str, MediaAsset]:
    """Available BGM downloads by format.

    WAV answers for the native key; every other entry is a stored transcode.
    """
    rows = session.exec(
        select(MediaAsset).where(
            MediaAsset.version_id == version_id,
            MediaAsset.kind.in_([AssetKind.MUSIC_BED, AssetKind.BGM_EXPORT]),
        )
    ).all()

    found: dict[str, MediaAsset] = {}
    for asset in rows:
        if asset.kind == AssetKind.MUSIC_BED:
            found["wav"] = asset
        else:
            found[asset.dedupe_key.rpartition(":")[2]] = asset
    return found


def _bgm_listing(session, version_id: str) -> list[ExportFormatOut]:
    available = _bgm_assets(session, version_id)
    return [
        ExportFormatOut(
            format=fmt,
            label=meta["label"],
            content_type=meta["content_type"],
            detail=meta["detail"],
            recommended=fmt == BGM_RECOMMENDED,
            ready=fmt in available,
            url=f"/api/media/{available[fmt].id}?download=1" if fmt in available else None,
            size_bytes=available[fmt].size_bytes if fmt in available else None,
        )
        for fmt, meta in BGM_FORMATS.items()
    ]


@router.get("/{story_id}/bgm/exports", response_model=list[ExportFormatOut])
def list_bgm_exports(story: OwnedStory, session: SessionDep) -> list[ExportFormatOut]:
    version = _current_version(story, session)
    return _bgm_listing(session, version.id)


@router.post("/{story_id}/bgm/exports", response_model=ExportOut)
def create_bgm_export(
    story: OwnedStory, body: ExportRequest, session: SessionDep, user: CurrentUser
) -> ExportOut:
    if body.format not in BGM_FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown BGM format: {body.format}")

    version = _current_version(story, session)
    available = _bgm_assets(session, version.id)

    if "wav" not in available:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this story has no background music yet; generate one first",
        )

    if asset := available.get(body.format):
        return ExportOut(
            format=body.format,
            ready=True,
            url=f"/api/media/{asset.id}?download=1",
            size_bytes=asset.size_bytes,
        )

    # WAV is always ready from the music_bed asset — no transcode needed.
    if body.format == "wav":
        wav = available["wav"]
        return ExportOut(
            format="wav",
            ready=True,
            url=f"/api/media/{wav.id}?download=1",
            size_bytes=wav.size_bytes,
        )

    dispatch_bgm_export(version_id=version.id, user_id=user.id, fmt=body.format)
    return ExportOut(format=body.format, ready=False, url=None, size_bytes=None)
