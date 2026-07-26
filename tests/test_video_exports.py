"""Downloading the pipeline's final video in other containers.

The endpoints themselves are thin, so what is worth testing is the decisions they
make: which formats are on offer, when a request is answered from an asset that
already exists, and when it costs a worker job. Getting the middle one wrong is
the expensive mistake — it would queue a VP9 encode of a whole episode for a file
already sitting in storage.

The format table is duplicated between the API and the agent on purpose (the API
image must not import the agent), so the two are asserted to agree here rather
than left to drift.
"""

import pytest
from daastaan_agent.assembly import VIDEO_EXPORTS, VIDEO_MASTER_FORMAT, video_content_type
from daastaan_api.routers import exports as exports_router
from daastaan_api.routers.exports import (
    VIDEO_FORMATS,
    VIDEO_RECOMMENDED,
    create_video_export,
    list_video_exports,
)
from daastaan_api.schemas import ExportRequest
from daastaan_common.models import MediaAsset, Story, StoryVersion, User
from daastaan_contracts import AssetKind
from fastapi import HTTPException

PASSWORD_HASH = "not-a-real-hash"  # noqa: S105 - never hashed or checked

# --- fixtures --------------------------------------------------------------


@pytest.fixture
def user(in_memory_session) -> User:
    account = User(id="user_1", email="a@b.c", password_hash=PASSWORD_HASH)
    in_memory_session.add(account)
    in_memory_session.commit()
    return account


@pytest.fixture
def story(in_memory_session, user) -> Story:
    story = Story(id="story_1", user_id="user_1", title="A Story", status="ready")
    version = StoryVersion(id="version_1", story_id="story_1", state_json={})
    story.current_version_id = "version_1"
    in_memory_session.add_all([story, version])
    in_memory_session.commit()
    return story


@pytest.fixture
def final_video(in_memory_session, story) -> MediaAsset:
    asset = MediaAsset(
        id="asset_master",
        version_id="version_1",
        dedupe_key="final_video:single",
        kind=AssetKind.FINAL_VIDEO.value,
        object_key="version_1/final_video/final.mp4",
        content_type="video/mp4",
        size_bytes=4096,
        duration_ms=60_000,
    )
    in_memory_session.add(asset)
    in_memory_session.commit()
    return asset


@pytest.fixture
def dispatched(monkeypatch) -> list[dict]:
    """Record dispatches instead of putting them on the queue."""
    calls: list[dict] = []

    def _record(*, version_id: str, user_id: str, fmt: str) -> str:
        calls.append({"version_id": version_id, "user_id": user_id, "fmt": fmt})
        return "task_1"

    monkeypatch.setattr(exports_router, "dispatch_video_export", _record)
    return calls


def _stored_export(session, fmt: str, *, asset_id: str = "asset_export") -> MediaAsset:
    asset = MediaAsset(
        id=asset_id,
        version_id="version_1",
        dedupe_key=f"{AssetKind.VIDEO_EXPORT.value}:{fmt}",
        kind=AssetKind.VIDEO_EXPORT.value,
        object_key=f"version_1/video_export/final.{fmt}",
        content_type=video_content_type(fmt),
        size_bytes=2048,
        duration_ms=60_000,
    )
    session.add(asset)
    session.commit()
    return asset


# --- the published table ---------------------------------------------------


class TestFormatTable:
    def test_the_api_and_the_worker_agree(self):
        """The API cannot import the agent, so the two tables are hand-kept in
        step. A format the API offers but the worker rejects is a button that
        always fails."""
        assert set(VIDEO_FORMATS) == set(VIDEO_EXPORTS) | {VIDEO_MASTER_FORMAT}

    def test_the_master_is_the_recommended_download(self):
        """Everything else is derived from it, so nothing can be better."""
        assert VIDEO_RECOMMENDED == VIDEO_MASTER_FORMAT

    def test_content_types_match_the_worker(self):
        """The listing tells the browser what it is about to receive; the worker
        stamps the stored object. A disagreement would serve a WebM labelled MP4."""
        for fmt, meta in VIDEO_FORMATS.items():
            assert meta["content_type"] == video_content_type(fmt)


# --- listing ---------------------------------------------------------------


class TestListing:
    def test_every_format_is_listed_with_the_master_ready(
        self, in_memory_session, story, final_video
    ):
        listing = list_video_exports(story, in_memory_session)

        assert [fmt.format for fmt in listing] == list(VIDEO_FORMATS)
        master = next(fmt for fmt in listing if fmt.format == "mp4")
        assert master.ready is True
        assert master.recommended is True
        assert master.url == "/api/media/asset_master?download=1"
        assert master.size_bytes == 4096

    def test_formats_with_nothing_stored_are_offered_but_not_ready(
        self, in_memory_session, story, final_video
    ):
        listing = list_video_exports(story, in_memory_session)

        for fmt in listing:
            if fmt.format == "mp4":
                continue
            assert fmt.ready is False
            assert fmt.url is None
            assert fmt.size_bytes is None

    def test_a_stored_transcode_becomes_ready(self, in_memory_session, story, final_video):
        _stored_export(in_memory_session, "webm")

        listing = list_video_exports(story, in_memory_session)

        webm = next(fmt for fmt in listing if fmt.format == "webm")
        assert webm.ready is True
        assert webm.url == "/api/media/asset_export?download=1"
        assert webm.size_bytes == 2048

    def test_nothing_is_ready_before_the_video_is_rendered(self, in_memory_session, story):
        listing = list_video_exports(story, in_memory_session)

        assert [fmt.format for fmt in listing] == list(VIDEO_FORMATS)
        assert all(fmt.ready is False for fmt in listing)

    def test_a_story_with_no_version_is_409(self, in_memory_session, story):
        story.current_version_id = None

        with pytest.raises(HTTPException) as exc:
            list_video_exports(story, in_memory_session)
        assert exc.value.status_code == 409


# --- requesting ------------------------------------------------------------


class TestRequesting:
    def test_an_unknown_format_is_refused(
        self, in_memory_session, story, user, final_video, dispatched
    ):
        """Rejected here rather than at the worker, so a typo is an error the
        caller sees instead of a job that dies in the queue."""
        with pytest.raises(HTTPException) as exc:
            create_video_export(story, ExportRequest(format="avi"), in_memory_session, user)

        assert exc.value.status_code == 400
        assert dispatched == []

    def test_a_story_with_no_video_is_409(self, in_memory_session, story, user, dispatched):
        with pytest.raises(HTTPException) as exc:
            create_video_export(story, ExportRequest(format="webm"), in_memory_session, user)

        assert exc.value.status_code == 409
        assert dispatched == []

    def test_mp4_is_served_from_the_master_without_a_job(
        self, in_memory_session, story, user, final_video, dispatched
    ):
        """Re-encoding the master into itself would spend minutes of CPU to
        produce a worse copy of a file already in storage."""
        out = create_video_export(story, ExportRequest(format="mp4"), in_memory_session, user)

        assert out.ready is True
        assert out.url == "/api/media/asset_master?download=1"
        assert out.size_bytes == 4096
        assert dispatched == []

    def test_a_format_that_has_to_be_made_is_queued(
        self, in_memory_session, story, user, final_video, dispatched
    ):
        out = create_video_export(story, ExportRequest(format="webm"), in_memory_session, user)

        assert out.ready is False
        assert out.url is None
        assert dispatched == [{"version_id": "version_1", "user_id": "user_1", "fmt": "webm"}]

    def test_a_format_already_made_is_not_queued_again(
        self, in_memory_session, story, user, final_video, dispatched
    ):
        _stored_export(in_memory_session, "mov")

        out = create_video_export(story, ExportRequest(format="mov"), in_memory_session, user)

        assert out.ready is True
        assert out.url == "/api/media/asset_export?download=1"
        assert dispatched == []


# --- carry-over ------------------------------------------------------------


def test_a_video_transcode_does_not_follow_a_regeneration():
    """The video it was made from is not carried over either, so keeping the
    transcode would offer a download of the previous version's footage under the
    new one."""
    from daastaan_common.versions import _TERMINAL_ASSET_KINDS

    assert AssetKind.VIDEO_EXPORT.value in _TERMINAL_ASSET_KINDS
