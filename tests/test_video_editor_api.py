"""The editor's API surface: derived state, share tokens, and uploads.

The interesting logic here is not the routing, it is what the endpoints *derive*.
`render_current` decides whether the Export button offers work or a download,
`version_current` decides whether a cut can be reopened at all, and the share
resolver decides who gets to watch a clip. Each is a small function with a
security or correctness consequence, so each is tested directly rather than
through a client.
"""

from datetime import UTC, datetime, timedelta

import pytest
from daastaan_api.routers.share import _resolve
from daastaan_api.routers.video_editor import (
    ASPECT_OPTIONS,
    CAPTION_PRESET_LABELS,
    FONT_OPTIONS,
    _audio_accepted,
    _edit_out,
    _extension,
    _local_audio_out,
    _validate_local_audio,
)
from daastaan_common.models import MediaAsset, Story, StoryVersion, User, VideoEdit
from daastaan_common.versions import carry_over_assets
from daastaan_contracts import (
    CAPTION_PRESETS,
    AspectRatio,
    AssetKind,
    AudioMix,
    CaptionFont,
    CaptionStyle,
    RenderStatus,
    Scope,
    StageName,
    StoryState,
    VideoEditManifest,
    manifest_digest,
)
from fastapi import HTTPException

TOKEN = "share-token-for-tests"  # noqa: S105 - fixture data, not a credential
PASSWORD_HASH = "not-a-real-hash"  # noqa: S105 - never hashed or checked

# --- fixtures --------------------------------------------------------------


@pytest.fixture
def story(in_memory_session) -> Story:
    user = User(id="user_1", email="a@b.c", password_hash=PASSWORD_HASH)
    story = Story(id="story_1", user_id="user_1", title="A Story", status="ready")
    version = StoryVersion(id="version_1", story_id="story_1", state_json={})
    story.current_version_id = "version_1"
    in_memory_session.add_all([user, story, version])
    in_memory_session.commit()
    return story


@pytest.fixture
def render(in_memory_session, story) -> MediaAsset:
    asset = MediaAsset(
        id="asset_1",
        version_id="version_1",
        dedupe_key="edited_video:abc",
        kind=AssetKind.EDITED_VIDEO.value,
        object_key="version_1/edited_video/abc.mp4",
        content_type="video/mp4",
        size_bytes=2048,
        duration_ms=30_000,
    )
    in_memory_session.add(asset)
    in_memory_session.commit()
    return asset


def make_edit(session, **overrides) -> VideoEdit:
    manifest = overrides.pop("manifest", VideoEditManifest())
    edit = VideoEdit(
        story_id="story_1",
        version_id="version_1",
        user_id="user_1",
        name="Cut 1",
        manifest_json=manifest.model_dump(mode="json"),
        **{"status": RenderStatus.DRAFT, **overrides},
    )
    session.add(edit)
    session.commit()
    session.refresh(edit)
    return edit


# --- render_current --------------------------------------------------------


class TestRenderCurrent:
    """Whether the MP4 on disk is the manifest currently on screen.

    This single flag drives the Export button, and getting it wrong in either
    direction is bad: a false positive offers a stale video as the finished cut, a
    false negative asks for an encode nobody needs.
    """

    def test_a_draft_with_no_render_is_not_current(self, in_memory_session, story):
        out = _edit_out(make_edit(in_memory_session), in_memory_session)

        assert out.render_current is False
        assert out.video_url is None
        assert out.download_url is None

    def test_a_render_matching_the_manifest_is_current(self, in_memory_session, story, render):
        manifest = VideoEditManifest()
        edit = make_edit(
            in_memory_session,
            manifest=manifest,
            render_asset_id=render.id,
            rendered_manifest_hash=manifest_digest(manifest),
            status=RenderStatus.READY,
        )

        out = _edit_out(edit, in_memory_session)

        assert out.render_current is True
        assert out.video_url == "/api/media/asset_1"
        assert out.download_url == "/api/media/asset_1?download=1"
        assert out.size_bytes == 2048
        assert out.duration_ms == 30_000

    def test_editing_after_a_render_makes_it_stale_but_keeps_the_download(
        self, in_memory_session, story, render
    ):
        """The old MP4 is still the last thing actually produced, so it stays
        downloadable while the editor knows it is behind."""
        edit = make_edit(
            in_memory_session,
            manifest=VideoEditManifest(aspect=AspectRatio.PORTRAIT),
            render_asset_id=render.id,
            rendered_manifest_hash=manifest_digest(VideoEditManifest()),
            status=RenderStatus.DRAFT,
        )

        out = _edit_out(edit, in_memory_session)

        assert out.render_current is False
        assert out.download_url == "/api/media/asset_1?download=1"

    def test_a_hash_without_an_asset_is_not_current(self, in_memory_session, story):
        """Guards the case where the row survived but the asset did not."""
        manifest = VideoEditManifest()
        edit = make_edit(
            in_memory_session,
            manifest=manifest,
            render_asset_id="asset_gone",
            rendered_manifest_hash=manifest_digest(manifest),
        )

        assert _edit_out(edit, in_memory_session).render_current is False


class TestVersionCurrent:
    """A cut belongs to the version it was cut from.

    Reopening one made against an older script would apply its trim to a timeline
    that no longer exists, so the editor archives it instead.
    """

    def test_a_cut_on_the_current_version_can_be_reopened(self, in_memory_session, story):
        assert _edit_out(make_edit(in_memory_session), in_memory_session).version_current is True

    def test_a_cut_from_an_earlier_version_cannot(self, in_memory_session, story):
        edit = make_edit(in_memory_session)
        edit.version_id = "version_0"
        in_memory_session.add(edit)
        in_memory_session.commit()

        assert _edit_out(edit, in_memory_session).version_current is False


# --- share links -----------------------------------------------------------


class TestShareUrl:
    def test_a_live_token_produces_a_link(self, in_memory_session, story):
        edit = make_edit(
            in_memory_session,
            share_token=TOKEN,
            share_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

        out = _edit_out(edit, in_memory_session)

        assert out.share_url is not None
        assert out.share_url.endswith(f"/#/share/{TOKEN}")

    def test_a_token_with_no_expiry_produces_a_link(self, in_memory_session, story):
        edit = make_edit(in_memory_session, share_token=TOKEN, share_expires_at=None)

        assert _edit_out(edit, in_memory_session).share_url is not None

    def test_an_expired_token_does_not(self, in_memory_session, story):
        """The editor must stop showing a link the public route has stopped
        serving, or it will be copied and sent."""
        edit = make_edit(
            in_memory_session,
            share_token=TOKEN,
            share_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        assert _edit_out(edit, in_memory_session).share_url is None


class TestShareResolution:
    """The public route's only credential is the token, so every rejection has to
    look the same. A distinguishable failure would turn this into an oracle for
    which tokens exist."""

    def _shared(self, session, **overrides):
        return make_edit(session, share_token=TOKEN, **overrides)

    def test_a_valid_token_resolves_to_its_cut(self, in_memory_session, story, render):
        edit = self._shared(in_memory_session, render_asset_id=render.id)

        resolved, asset, found = _resolve(in_memory_session, TOKEN)

        assert resolved.id == edit.id
        assert asset.id == render.id
        assert found.id == "story_1"

    @pytest.mark.parametrize("token", ["", "unknown", "x" * 200])
    def test_a_token_that_names_nothing_is_404(self, in_memory_session, story, token):
        with pytest.raises(HTTPException) as exc:
            _resolve(in_memory_session, token)
        assert exc.value.status_code == 404

    def test_an_expired_token_is_404(self, in_memory_session, story, render):
        self._shared(
            in_memory_session,
            render_asset_id=render.id,
            share_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        with pytest.raises(HTTPException) as exc:
            _resolve(in_memory_session, TOKEN)
        assert exc.value.status_code == 404

    def test_a_token_on_an_unrendered_cut_is_404(self, in_memory_session, story):
        """Revocation clears the token, but a cut whose render never landed would
        otherwise resolve to a row with nothing to play."""
        self._shared(in_memory_session, render_asset_id=None)

        with pytest.raises(HTTPException) as exc:
            _resolve(in_memory_session, TOKEN)
        assert exc.value.status_code == 404

    def test_an_asset_with_no_object_behind_it_is_404(self, in_memory_session, story):
        empty = MediaAsset(
            id="asset_empty",
            version_id="version_1",
            dedupe_key="edited_video:empty",
            kind=AssetKind.EDITED_VIDEO.value,
            object_key="",
            content_type="video/mp4",
        )
        in_memory_session.add(empty)
        in_memory_session.commit()
        self._shared(in_memory_session, render_asset_id="asset_empty")

        with pytest.raises(HTTPException) as exc:
            _resolve(in_memory_session, TOKEN)
        assert exc.value.status_code == 404


# --- backing tracks --------------------------------------------------------


class TestAudioSniffing:
    """The browser's `type` on an upload is derived from the file extension, which
    makes it the client's opinion rather than a fact about the bytes."""

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (b"ID3\x04\x00" + b"\x00" * 20, "audio/mpeg"),
            (b"RIFF\x24\x08\x00\x00WAVEfmt ", "audio/wav"),
            (b"OggS\x00\x02" + b"\x00" * 20, "audio/ogg"),
            (b"fLaC\x00\x00\x00\x22", "audio/flac"),
            (b"\x00\x00\x00\x20ftypM4A ", "audio/mp4"),
            # A bare MP3 with no ID3 tag: frame sync only.
            (b"\xff\xfb\x90\x00" + b"\x00" * 20, "audio/mpeg"),
        ],
    )
    def test_recognised_containers(self, data, expected):
        assert _audio_accepted(data) == expected

    @pytest.mark.parametrize(
        "data",
        [
            b"\x89PNG\r\n\x1a\n",
            b"%PDF-1.7",
            b"<html><body>hi</body></html>",
            b"",
            b"\x00\x01",
        ],
    )
    def test_anything_else_is_refused(self, data):
        assert _audio_accepted(data) is None

    def test_an_executable_renamed_to_mp3_is_still_refused(self):
        assert _audio_accepted(b"\x7fELF\x02\x01\x01\x00") is None


class TestExtension:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("song.mp3", ".mp3"),
            ("SONG.MP3", ".mp3"),
            ("my.song.flac", ".flac"),
            ("noextension", ""),
            ("trailing.", "."),
        ],
    )
    def test_lowercased_last_suffix(self, filename, expected):
        assert _extension(filename) == expected


class TestLocalAudioValidation:
    """Without this the asset id in a manifest is a way to read any upload on the
    system: the worker fetches whatever it is given and the rendered cut carries
    the audio back out."""

    def _track(self, session, version_id="version_1", kind=AssetKind.LOCAL_AUDIO):
        asset = MediaAsset(
            version_id=version_id,
            dedupe_key=f"{kind.value}:t",
            kind=kind.value,
            object_key=f"{version_id}/t.bin",
            content_type="audio/mpeg",
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset

    def test_no_track_named_is_always_fine(self, in_memory_session, story):
        _validate_local_audio(in_memory_session, "version_1", VideoEditManifest())

    def test_this_version_own_track_is_accepted(self, in_memory_session, story):
        track = self._track(in_memory_session)
        manifest = VideoEditManifest(audio=AudioMix(local_asset_id=track.id))

        _validate_local_audio(in_memory_session, "version_1", manifest)

    def test_another_version_track_is_refused(self, in_memory_session, story):
        track = self._track(in_memory_session, version_id="version_other")
        manifest = VideoEditManifest(audio=AudioMix(local_asset_id=track.id))

        with pytest.raises(HTTPException) as exc:
            _validate_local_audio(in_memory_session, "version_1", manifest)
        assert exc.value.status_code == 400

    def test_an_asset_of_another_kind_is_refused(self, in_memory_session, story):
        """Otherwise a manifest could name someone's narration and have it mixed
        into a downloadable cut."""
        track = self._track(in_memory_session, kind=AssetKind.LINE_AUDIO)
        manifest = VideoEditManifest(audio=AudioMix(local_asset_id=track.id))

        with pytest.raises(HTTPException) as exc:
            _validate_local_audio(in_memory_session, "version_1", manifest)
        assert exc.value.status_code == 400

    def test_an_unknown_id_is_refused(self, in_memory_session, story):
        manifest = VideoEditManifest(audio=AudioMix(local_asset_id="nope"))

        with pytest.raises(HTTPException) as exc:
            _validate_local_audio(in_memory_session, "version_1", manifest)
        assert exc.value.status_code == 400


def test_an_uploaded_filename_is_shown_but_never_used_as_a_key():
    """`object_key` is minted; the original name is display text only."""
    asset = MediaAsset(
        id="asset_2",
        version_id="version_1",
        dedupe_key="local_audio:t",
        kind=AssetKind.LOCAL_AUDIO.value,
        object_key="version_1/local_audio/t.bin",
        content_type="audio/mpeg",
        instructions_used="my song.mp3",
        size_bytes=1024,
    )

    out = _local_audio_out(asset)

    assert out.filename == "my song.mp3"
    assert out.url == "/api/media/asset_2"


# --- carry-over ------------------------------------------------------------


class TestCarryOver:
    def _asset(self, session, kind: AssetKind, key: str) -> MediaAsset:
        asset = MediaAsset(
            version_id="version_1",
            dedupe_key=f"{kind.value}:{key}",
            kind=kind.value,
            object_key=f"version_1/{key}",
            content_type="application/octet-stream",
        )
        session.add(asset)
        return asset

    def _fork(self, session) -> list[MediaAsset]:
        from sqlmodel import select

        carry_over_assets(
            session,
            parent_version_id="version_1",
            child_version_id="version_2",
            state=StoryState(
                story_id="story_1", version_id="version_1", user_id="user_1", raw_text="x"
            ),
            scope=Scope.MUSIC,
            planned=[StageName.MUSIC_GENERATION],
            target_id=None,
        )
        session.commit()
        return list(
            session.exec(select(MediaAsset).where(MediaAsset.version_id == "version_2")).all()
        )

    def test_an_uploaded_track_follows_the_fork(self, in_memory_session, story):
        """Nothing in the pipeline produced it, so no regeneration invalidates it -
        and a listener should not have to upload their song again to respeak a
        line."""
        self._asset(in_memory_session, AssetKind.LOCAL_AUDIO, "track.bin")
        in_memory_session.commit()

        assert [asset.kind for asset in self._fork(in_memory_session)] == [
            AssetKind.LOCAL_AUDIO.value
        ]

    def test_an_edited_cut_does_not(self, in_memory_session, story):
        """A cut is a render of one version's footage and its `video_edits` row
        still points there. Copying the MP4 forward would put a second row for the
        same render on a version whose script may no longer match it."""
        self._asset(in_memory_session, AssetKind.EDITED_VIDEO, "cut.mp4")
        in_memory_session.commit()

        assert self._fork(in_memory_session) == []


# --- published options -----------------------------------------------------


def test_every_font_the_manifest_allows_has_a_label():
    """The editor renders whatever this publishes, so a missing entry is a choice
    the renderer supports and nobody can pick."""
    assert set(FONT_OPTIONS) == set(CaptionFont)


def test_every_aspect_the_manifest_allows_has_a_label():
    assert set(ASPECT_OPTIONS) == set(AspectRatio)


def test_every_caption_preset_has_a_label():
    assert set(CAPTION_PRESET_LABELS) == set(CAPTION_PRESETS)


def test_presets_are_complete_caption_styles():
    """Applying a preset replaces the whole style, so a partial one would carry
    fields over from whatever was set before."""
    for style in CAPTION_PRESETS.values():
        assert isinstance(style, CaptionStyle)
        assert CaptionStyle.model_validate(style.model_dump()) == style
