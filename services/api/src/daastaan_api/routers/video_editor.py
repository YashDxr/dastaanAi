"""The video editor: cuts of a finished episode.

A *cut* is a manifest, not a file. Saving one is cheap and immediate; rendering
it is a worker job, so this endpoint accepts the request and the client polls -
the same shape as `exports`, for the same reason.

Cuts deliberately live outside the pipeline. Rendering one never touches
`StoryState` or the story's status, and a failed export leaves a story that plays
perfectly well looking exactly as healthy as it is. That is also why this is a
separate router rather than more routes on `stories`.
"""

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from daastaan_common import get_settings, get_store, ids
from daastaan_common.models import MediaAsset, Story, StoryVersion, VideoEdit, as_utc
from daastaan_contracts import (
    ASPECT_SIZES,
    CAPTION_PRESETS,
    AspectRatio,
    AssetKind,
    CaptionFont,
    RenderStatus,
    StoryState,
    UserRole,
    VideoEditManifest,
    build_timeline,
    limits,
    manifest_digest,
    timeline_duration_ms,
)
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlmodel import Session, func, select

from ..deps import CurrentUser, OwnedStory, SessionDep
from ..dispatch import dispatch_video_edit_render
from ..guards import audit, enforce_rate_limit
from ..schemas import (
    AspectOptionOut,
    CaptionCueOut,
    CaptionPresetOut,
    EditorCapabilitiesOut,
    FontOptionOut,
    LocalAudioOut,
    ShareOut,
    ShareRequest,
    VideoEditCreateRequest,
    VideoEditorOut,
    VideoEditOut,
    VideoEditUpdateRequest,
)

log = structlog.get_logger(__name__)

router = APIRouter(tags=["video-editor"])

# Copy of the choices `daastaan_agent.video_edit` knows how to render, with the
# labels the editor shows. The API cannot import the agent package - dispatching
# by task name is the whole point - so the list is duplicated here and the worker
# validates against the same enum.
FONT_OPTIONS: dict[CaptionFont, tuple[str, str]] = {
    CaptionFont.SANS: ("Clean Sans", "Neutral and highly legible. The safe default."),
    CaptionFont.SERIF: ("Story Serif", "Warmer and more literary. Suits period and folk tales."),
    CaptionFont.MONO: ("Screenplay", "Fixed width, like a script page."),
    CaptionFont.NOTO_SANS: ("Noto Sans", "Widest script coverage. Use for Hindi and others."),
    CaptionFont.NOTO_SERIF: ("Noto Serif", "Serif with broad script coverage."),
}

ASPECT_OPTIONS: dict[AspectRatio, tuple[str, str]] = {
    AspectRatio.LANDSCAPE: ("Landscape", "YouTube and everything that expects a wide frame."),
    AspectRatio.PORTRAIT: ("Vertical", "Reels, Shorts and TikTok. Fills a phone screen."),
    AspectRatio.SQUARE: ("Square", "Feed posts that must read the same cropped or not."),
    AspectRatio.PORTRAIT_4_5: ("Portrait", "Tallest frame a feed will show without cropping."),
}

CAPTION_PRESET_LABELS: dict[str, tuple[str, str]] = {
    "clean": ("Clean", "White text in a soft box. Readable over anything."),
    "bold_social": ("Bold social", "Big centred capitals, no box. Built for muted autoplay."),
    "cinematic": ("Cinematic", "Small serif low in the frame, no box."),
    "screenplay": ("Screenplay", "Fixed-width with speaker names, like a script."),
    "off": ("No captions", "Picture and sound only."),
}

# Advisory: the magic-byte check below is what actually decides.
ALLOWED_AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".wav", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".flac"}
)

_AUDIO_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"ID3", "audio/mpeg"),
    (b"RIFF", "audio/wav"),
    (b"OggS", "audio/ogg"),
    (b"fLaC", "audio/flac"),
)

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9 ._-]+")


# --- helpers ---------------------------------------------------------------


def owned_edit(edit_id: str, session: SessionDep, user: CurrentUser) -> VideoEdit:
    """Load a cut the caller may act on.

    404 rather than 403 for someone else's cut, matching `owned_story`: the
    endpoint should not confirm that an id it will not serve exists.
    """
    edit = session.get(VideoEdit, edit_id)
    if edit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cut not found")
    if edit.user_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cut not found")
    return edit


OwnedEdit = Annotated[VideoEdit, Depends(owned_edit)]


def _current_version(story: Story, session: Session) -> StoryVersion:
    version = (
        session.get(StoryVersion, story.current_version_id)
        if story.current_version_id
        else None
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this story has no finished episode yet")
    return version


def _share_url(token: str) -> str:
    """The absolute link handed to whoever is doing the sharing.

    Built from the configured web origin rather than from the request, so a link
    minted through a proxy or a tunnel still points somewhere a recipient can
    reach.
    """
    return f"{get_settings().web_base_url.rstrip('/')}/#/share/{token}"


def _edit_out(edit: VideoEdit, session: Session) -> VideoEditOut:
    manifest = VideoEditManifest.model_validate(edit.manifest_json)
    asset = session.get(MediaAsset, edit.render_asset_id) if edit.render_asset_id else None
    story = session.get(Story, edit.story_id)
    expired = (
        edit.share_expires_at is not None
        and as_utc(edit.share_expires_at) <= datetime.now(UTC)
    )
    return VideoEditOut(
        id=edit.id,
        story_id=edit.story_id,
        version_id=edit.version_id,
        name=edit.name,
        manifest=manifest,
        status=edit.status,
        error=edit.error,
        video_url=f"/api/media/{asset.id}" if asset else None,
        download_url=f"/api/media/{asset.id}?download=1" if asset else None,
        size_bytes=asset.size_bytes if asset else None,
        duration_ms=asset.duration_ms if asset else None,
        render_current=bool(
            asset is not None and edit.rendered_manifest_hash == manifest_digest(manifest)
        ),
        version_current=bool(story is not None and story.current_version_id == edit.version_id),
        share_url=(
            _share_url(edit.share_token) if edit.share_token and not expired else None
        ),
        # Normalised on the way out too: without an offset the browser reads the
        # timestamp as local time, which moves the displayed expiry by hours.
        share_expires_at=as_utc(edit.share_expires_at) if edit.share_expires_at else None,
        share_views=edit.share_views,
        created_at=edit.created_at,
        updated_at=edit.updated_at,
    )


def _line_audio(session: Session, version_id: str) -> dict[str, MediaAsset]:
    return {
        asset.line_id: asset
        for asset in session.exec(
            select(MediaAsset).where(
                MediaAsset.version_id == version_id,
                MediaAsset.kind == AssetKind.LINE_AUDIO.value,
                MediaAsset.object_key != "",
            )
        ).all()
        if asset.line_id
    }


def _artwork(session: Session, version_id: str) -> dict[str, MediaAsset]:
    """Scene artwork by the slot it fills: a line id where the pipeline made one
    image per line, a scene id where it made one per scene."""
    found: dict[str, MediaAsset] = {}
    for asset in session.exec(
        select(MediaAsset).where(
            MediaAsset.version_id == version_id,
            MediaAsset.kind == AssetKind.SCENE_IMAGE.value,
            MediaAsset.object_key != "",
        )
    ).all():
        if slot := (asset.line_id or asset.scene_id):
            found[slot] = asset
    return found


def _first_asset(session: Session, version_id: str, kind: AssetKind) -> MediaAsset | None:
    return session.exec(
        select(MediaAsset).where(
            MediaAsset.version_id == version_id,
            MediaAsset.kind == kind.value,
            MediaAsset.object_key != "",
        )
    ).first()


# --- opening the editor ----------------------------------------------------


@router.get("/stories/{story_id}/editor", response_model=VideoEditorOut)
def open_editor(story: OwnedStory, session: SessionDep) -> VideoEditorOut:
    """Everything the editor needs in one request.

    One round trip rather than six, because the editor is opened from a link and
    has nothing on screen until all of it has arrived: the timeline, the artwork,
    the saved cuts, the uploaded tracks, and the style choices this build offers.
    """
    version = _current_version(story, session)
    state = StoryState.model_validate(version.state_json)

    audio = _line_audio(session, version.id)
    artwork = _artwork(session, version.id)
    spans = build_timeline(
        state.lines, {line_id: asset.duration_ms or 0 for line_id, asset in audio.items()}
    )

    cues = [
        CaptionCueOut(
            line_id=span.line_id,
            scene_id=span.scene_id,
            index=span.index,
            start_ms=span.start_ms,
            end_ms=span.end_ms,
            caption_end_ms=span.caption_end_ms,
            speaker=span.speaker,
            text=span.text,
            image_url=(
                f"/api/media/{image.id}"
                if (image := artwork.get(span.line_id) or artwork.get(span.scene_id))
                else None
            ),
        )
        for span in spans
    ]

    episode = _first_asset(session, version.id, AssetKind.FINAL_EPISODE)
    source_video = _first_asset(session, version.id, AssetKind.FINAL_VIDEO)
    score = _first_asset(session, version.id, AssetKind.MUSIC_BED)

    edits = session.exec(
        select(VideoEdit)
        .where(VideoEdit.story_id == story.id)
        .order_by(VideoEdit.created_at)
    ).all()

    tracks = session.exec(
        select(MediaAsset)
        .where(
            MediaAsset.version_id == version.id,
            MediaAsset.kind == AssetKind.LOCAL_AUDIO.value,
            MediaAsset.object_key != "",
        )
        .order_by(MediaAsset.created_at)
    ).all()

    return VideoEditorOut(
        story_id=story.id,
        version_id=version.id,
        title=story.title,
        capabilities=EditorCapabilitiesOut(
            has_score=score is not None,
            has_artwork=bool(artwork),
            line_count=len(spans),
            duration_ms=timeline_duration_ms(spans),
        ),
        cues=cues,
        source_video_url=f"/api/media/{source_video.id}" if source_video else None,
        episode_audio_url=f"/api/media/{episode.id}" if episode else None,
        edits=[_edit_out(edit, session) for edit in edits],
        local_audio=[_local_audio_out(track) for track in tracks],
        caption_presets=[
            CaptionPresetOut(
                key=key,
                label=CAPTION_PRESET_LABELS[key][0],
                detail=CAPTION_PRESET_LABELS[key][1],
                style=style.model_dump(mode="json"),
            )
            for key, style in CAPTION_PRESETS.items()
        ],
        fonts=[
            FontOptionOut(key=font.value, label=label, detail=detail)
            for font, (label, detail) in FONT_OPTIONS.items()
        ],
        aspects=[
            AspectOptionOut(
                key=aspect.value,
                label=label,
                detail=detail,
                width=ASPECT_SIZES[aspect][0],
                height=ASPECT_SIZES[aspect][1],
            )
            for aspect, (label, detail) in ASPECT_OPTIONS.items()
        ],
    )


# --- cuts ------------------------------------------------------------------


@router.post(
    "/stories/{story_id}/editor/cuts",
    response_model=VideoEditOut,
    status_code=status.HTTP_201_CREATED,
)
def create_cut(
    story: OwnedStory,
    body: VideoEditCreateRequest,
    session: SessionDep,
    user: CurrentUser,
) -> VideoEditOut:
    version = _current_version(story, session)

    existing = session.exec(
        select(func.count()).select_from(VideoEdit).where(VideoEdit.story_id == story.id)
    ).one()
    if existing >= limits.MAX_EDITS_PER_STORY:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"this story already has {limits.MAX_EDITS_PER_STORY} cuts; delete one first",
        )

    manifest = body.manifest or VideoEditManifest()
    _validate_local_audio(session, version.id, manifest)

    edit = VideoEdit(
        story_id=story.id,
        version_id=version.id,
        user_id=user.id,
        name=body.name.strip() or f"Cut {existing + 1}",
        manifest_json=manifest.model_dump(mode="json"),
        status=RenderStatus.DRAFT,
    )
    session.add(edit)
    session.commit()
    session.refresh(edit)
    return _edit_out(edit, session)


@router.get("/editor/cuts/{edit_id}", response_model=VideoEditOut)
def get_cut(edit: OwnedEdit, session: SessionDep) -> VideoEditOut:
    """Polled while a render is in flight, so it stays deliberately cheap."""
    return _edit_out(edit, session)


@router.patch("/editor/cuts/{edit_id}", response_model=VideoEditOut)
def update_cut(
    edit: OwnedEdit, body: VideoEditUpdateRequest, session: SessionDep
) -> VideoEditOut:
    """Save a change to a cut.

    A manifest change puts the cut back to `draft` while leaving the previous
    render in place: the old MP4 is still the last thing that was actually
    produced and stays downloadable, but the editor now knows it is behind.
    """
    if body.name is not None:
        edit.name = body.name.strip() or edit.name

    if body.manifest is not None:
        _validate_local_audio(session, edit.version_id, body.manifest)
        edit.manifest_json = body.manifest.model_dump(mode="json")
        if edit.status != RenderStatus.RENDERING:
            edit.status = (
                RenderStatus.READY
                if edit.rendered_manifest_hash == manifest_digest(body.manifest)
                else RenderStatus.DRAFT
            )
            edit.error = None

    edit.updated_at = datetime.now(UTC)
    session.add(edit)
    session.commit()
    session.refresh(edit)
    return _edit_out(edit, session)


@router.delete("/editor/cuts/{edit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cut(edit: OwnedEdit, session: SessionDep, user: CurrentUser) -> None:
    """Forget a cut.

    The rendered MP4 is left behind on purpose. It is an ordinary media asset
    that a share link or an open tab may still be resolving, and the storage
    backends offer no delete - so removing the row and leaving the bytes is the
    honest version of this operation rather than a half-done one.
    """
    audit(
        session,
        actor_user_id=user.id,
        action="video_edit.delete",
        target_type="video_edit",
        target_id=edit.id,
    )
    session.delete(edit)
    session.commit()


@router.post(
    "/editor/cuts/{edit_id}/render",
    response_model=VideoEditOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def render_cut(edit: OwnedEdit, session: SessionDep, user: CurrentUser) -> VideoEditOut:
    """Queue an encode.

    Rate limited rather than budget capped: a render spends CPU on the assembly
    pool, not credit at a paid API, so what needs bounding is how much queue one
    person can hold.
    """
    manifest = VideoEditManifest.model_validate(edit.manifest_json)
    digest = manifest_digest(manifest)

    if edit.status == RenderStatus.RENDERING:
        # Already in flight. Returning the row rather than 409 keeps a double
        # click on the Export button harmless.
        return _edit_out(edit, session)

    if edit.rendered_manifest_hash == digest and edit.render_asset_id:
        existing = session.get(MediaAsset, edit.render_asset_id)
        if existing is not None:
            edit.status = RenderStatus.READY
            session.add(edit)
            session.commit()
            session.refresh(edit)
            return _edit_out(edit, session)

    _validate_local_audio(session, edit.version_id, manifest)
    enforce_rate_limit(session, user.id, "video_render", limits.RATE_LIMIT_RENDERS)

    edit.status = RenderStatus.QUEUED
    edit.error = None
    edit.updated_at = datetime.now(UTC)
    session.add(edit)
    audit(
        session,
        actor_user_id=user.id,
        action="video_edit.render",
        target_type="video_edit",
        target_id=edit.id,
        metadata={"aspect": manifest.aspect.value, "digest": digest},
    )
    session.commit()

    dispatch_video_edit_render(edit_id=edit.id, user_id=user.id)
    session.refresh(edit)
    return _edit_out(edit, session)


# --- sharing ---------------------------------------------------------------


@router.post("/editor/cuts/{edit_id}/share", response_model=ShareOut)
def share_cut(
    edit: OwnedEdit, body: ShareRequest, session: SessionDep, user: CurrentUser
) -> ShareOut:
    """Mint a public link to a rendered cut.

    The token is the whole credential, so it is 32 bytes from `secrets` and is
    the only thing the public route accepts. Sharing is refused until a render
    exists, because a link that resolves to nothing is worse than no link.

    Re-sharing reuses the existing token and only moves the expiry. Rotating it
    would silently break every link already sent, which is not what pressing
    Share again means.
    """
    if not edit.render_asset_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "export this cut before sharing it"
        )

    now = datetime.now(UTC)
    if not edit.share_token:
        edit.share_token = secrets.token_urlsafe(32)
        edit.share_created_at = now
        edit.share_views = 0

    edit.share_expires_at = (
        now + timedelta(hours=body.expires_in_hours) if body.expires_in_hours else None
    )
    edit.updated_at = now
    session.add(edit)
    audit(
        session,
        actor_user_id=user.id,
        action="video_edit.share",
        target_type="video_edit",
        target_id=edit.id,
        metadata={"expires_in_hours": body.expires_in_hours},
    )
    session.commit()

    return ShareOut(
        share_url=_share_url(edit.share_token), expires_at=edit.share_expires_at
    )


@router.delete("/editor/cuts/{edit_id}/share", status_code=status.HTTP_204_NO_CONTENT)
def revoke_share(edit: OwnedEdit, session: SessionDep, user: CurrentUser) -> None:
    """Withdraw a link. The token is cleared rather than expired, so it can never
    be reinstated by moving a date."""
    edit.share_token = None
    edit.share_expires_at = None
    edit.share_created_at = None
    edit.updated_at = datetime.now(UTC)
    session.add(edit)
    audit(
        session,
        actor_user_id=user.id,
        action="video_edit.unshare",
        target_type="video_edit",
        target_id=edit.id,
    )
    session.commit()


# --- backing tracks --------------------------------------------------------


def _local_audio_out(asset: MediaAsset) -> LocalAudioOut:
    return LocalAudioOut(
        id=asset.id,
        # The original name is kept in `instructions_used` rather than in a column
        # of its own: it is display text for one asset kind, and `media_assets` is
        # not the place to grow a field that only ever applies to uploads.
        filename=asset.instructions_used or "backing track",
        content_type=asset.content_type,
        duration_ms=asset.duration_ms,
        size_bytes=asset.size_bytes,
        url=f"/api/media/{asset.id}",
    )


def _audio_accepted(data: bytes) -> str | None:
    """The content type these bytes actually are, or None if unrecognised.

    Sniffed rather than trusted: the browser's `type` on an upload is taken from
    the file extension, which is the client's opinion. MPEG-4 audio carries its
    brand a few bytes in, and a bare MP3 has no magic at all beyond the frame
    sync bits, so both are checked explicitly.
    """
    for signature, content_type in _AUDIO_MAGIC:
        if data.startswith(signature):
            return content_type
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    # MPEG audio frame sync: eleven set bits opening the header.
    if len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    return None


def _extension(filename: str) -> str:
    _, dot, suffix = filename.rpartition(".")
    return f".{suffix.lower()}" if dot else ""


@router.post(
    "/stories/{story_id}/editor/audio",
    response_model=LocalAudioOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_backing_track(
    story: OwnedStory,
    session: SessionDep,
    user: CurrentUser,
    file: UploadFile = File(...),
) -> LocalAudioOut:
    """Store a track the user wants under their cut.

    Kept as a `local_audio` media asset on the current version, which means it is
    served, authorised and range-requested by exactly the same code as everything
    the pipeline produced. It is also why `versions.carry_over_assets` must not
    treat it as reusable output: nobody generated it.
    """
    version = _current_version(story, session)

    filename = (file.filename or "track").strip()[:120]
    extension = _extension(filename)
    if extension and extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"{extension} files are not supported. Upload an MP3, WAV, M4A, FLAC or Opus file.",
        )

    data = await file.read(limits.MAX_LOCAL_AUDIO_BYTES + 1)
    if len(data) > limits.MAX_LOCAL_AUDIO_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file is larger than {limits.MAX_LOCAL_AUDIO_BYTES // (1024 * 1024)} MB",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is empty")

    content_type = _audio_accepted(data)
    if content_type is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "That file is not audio we can read. Upload an MP3, WAV, M4A, FLAC or Opus file.",
        )

    # The key is minted, never derived from the filename: `validate_key` would
    # reject most of what a filename can hold, and what it would accept is still
    # a path the client chose.
    tag = ids.new_id()
    key = ids.object_key(version.id, AssetKind.LOCAL_AUDIO, tag=tag, ext="bin")
    get_store().put(key, data, content_type)

    asset = MediaAsset(
        version_id=version.id,
        dedupe_key=f"{AssetKind.LOCAL_AUDIO.value}:{tag}",
        kind=AssetKind.LOCAL_AUDIO.value,
        object_key=key,
        content_type=content_type,
        instructions_used=_UNSAFE_FILENAME.sub(" ", filename).strip() or "backing track",
        size_bytes=len(data),
    )
    session.add(asset)
    audit(
        session,
        actor_user_id=user.id,
        action="video_edit.audio_upload",
        target_type="media_asset",
        target_id=asset.id,
        metadata={"size_bytes": len(data), "content_type": content_type},
    )
    session.commit()
    session.refresh(asset)
    return _local_audio_out(asset)


def _validate_local_audio(
    session: Session, version_id: str, manifest: VideoEditManifest
) -> None:
    """Reject a manifest naming a track that is not this version's.

    Without this the asset id in a manifest would be a way to read any upload on
    the system: the worker fetches whatever it is given, and the rendered cut
    would carry the audio back out. Checked again in the worker, because a
    manifest can be rendered long after it was saved.
    """
    asset_id = manifest.audio.local_asset_id
    if not asset_id:
        return
    asset = session.get(MediaAsset, asset_id)
    if (
        asset is None
        or asset.version_id != version_id
        or asset.kind != AssetKind.LOCAL_AUDIO.value
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "that backing track does not belong to this story"
        )
