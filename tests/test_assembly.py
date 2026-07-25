"""Tests for scene timeline calculation and video composition."""

import io
import shutil
import struct

import pytest
from daastaan_agent.assembly import (
    AssemblyError,
    SceneFrame,
    build_scene_timeline,
    compose_video,
)


# --- helpers --------------------------------------------------------------


def _make_line(line_id: str, scene_id: str, index: int, pause_after_ms: int = 0):
    """Minimal line-like object with the fields build_scene_timeline reads."""
    return type("Line", (), {
        "id": line_id,
        "scene_id": scene_id,
        "index": index,
        "pause_after_ms": pause_after_ms,
    })()


def _make_asset(duration_ms: int):
    """Minimal asset-like object with duration_ms."""
    return type("Asset", (), {"duration_ms": duration_ms})()


def _minimal_png() -> bytes:
    """A valid 1x1 red PNG image."""
    # Minimal valid PNG: 1x1 pixel, RGB
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        raw = chunk_type + data
        return struct.pack(">I", len(data)) + raw + struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1 RGB
    raw_data = b"\x00\xff\x00\x00"  # filter byte + R G B
    idat = zlib.compress(raw_data)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _silent_mp3() -> bytes:
    """A tiny valid MP3 file (silent, ~0.1s)."""
    # Minimal MPEG audio frame: MPEG1 Layer3 128kbps 44100Hz stereo
    # Frame header: 0xFFFB9004
    header = b"\xff\xfb\x90\x04"
    # A single frame is 417 bytes for 128kbps/44100Hz
    frame = header + b"\x00" * 413
    # Repeat a few frames to ensure ffmpeg can decode it
    return frame * 10


# --- build_scene_timeline -------------------------------------------------


class TestBuildSceneTimeline:
    def test_basic_timeline(self):
        lines = [
            _make_line("l1", "s1", 0, pause_after_ms=200),
            _make_line("l2", "s1", 1, pause_after_ms=100),
            _make_line("l3", "s2", 2, pause_after_ms=0),
        ]
        assets = {
            "l1": _make_asset(1000),
            "l2": _make_asset(500),
            "l3": _make_asset(800),
        }
        result = build_scene_timeline(lines, assets)

        assert "s1" in result
        assert "s2" in result
        # s1: l1(1000+200) + l2(500+100) = 1800ms total
        assert result["s1"] == (0, 1800)
        # s2: starts at 1800, l3(800+0) = 800ms
        assert result["s2"] == (1800, 2600)

    def test_missing_audio_assets(self):
        """Lines without audio assets contribute 0ms duration."""
        lines = [
            _make_line("l1", "s1", 0, pause_after_ms=100),
            _make_line("l2", "s1", 1, pause_after_ms=0),
        ]
        assets = {
            "l1": _make_asset(1000),
            # l2 has no asset
        }
        result = build_scene_timeline(lines, assets)

        # s1: l1(1000+100) + l2(0+0) = 1100ms
        assert result["s1"] == (0, 1100)

    def test_empty_lines(self):
        result = build_scene_timeline([], {})
        assert result == {}

    def test_scenes_ordered_by_first_line_index(self):
        """Scenes should be ordered by their earliest line index."""
        lines = [
            _make_line("l1", "s2", 2, pause_after_ms=0),
            _make_line("l2", "s1", 0, pause_after_ms=0),
            _make_line("l3", "s1", 1, pause_after_ms=0),
        ]
        assets = {
            "l1": _make_asset(500),
            "l2": _make_asset(300),
            "l3": _make_asset(400),
        }
        result = build_scene_timeline(lines, assets)

        # s1 comes first (line index 0), s2 comes second (line index 2)
        assert result["s1"] == (0, 700)
        assert result["s2"] == (700, 1200)


# --- compose_video --------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not installed",
)
class TestComposeVideo:
    def test_returns_mp4(self):
        """compose_video should return bytes starting with the MP4 ftyp box."""
        audio = _silent_mp3()
        frames = [
            SceneFrame(image=_minimal_png(), duration_ms=500, scene_id="s1"),
            SceneFrame(image=_minimal_png(), duration_ms=500, scene_id="s2"),
        ]
        result = compose_video(audio, frames)

        assert isinstance(result, bytes)
        assert len(result) > 0
        # MP4 files have 'ftyp' within the first 12 bytes
        assert b"ftyp" in result[:12]

    def test_empty_frames_raises(self):
        with pytest.raises(AssemblyError, match="no scene frames"):
            compose_video(b"audio", [])
