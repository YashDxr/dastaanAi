"""Byte-range parsing for the media endpoint.

An audio element that cannot issue range requests has to refetch from byte zero
to move the playhead, which the listener experiences as the track restarting. So
these cases are the difference between a working scrubber and a broken one.
"""

import pytest
from daastaan_api.routers.media import parse_range
from fastapi import HTTPException

SIZE = 1000


class TestUsableRanges:
    def test_open_ended(self):
        assert parse_range("bytes=0-", SIZE) == (0, 999)

    def test_closed(self):
        assert parse_range("bytes=100-199", SIZE) == (100, 199)

    def test_mid_file_open_ended(self):
        """What a browser sends after the user drags the scrubber forward."""
        assert parse_range("bytes=500-", SIZE) == (500, 999)

    def test_suffix(self):
        assert parse_range("bytes=-200", SIZE) == (800, 999)

    def test_end_past_eof_is_clamped(self):
        assert parse_range("bytes=900-5000", SIZE) == (900, 999)

    def test_whitespace_tolerated(self):
        assert parse_range(" bytes=0-99 ", SIZE) == (0, 99)


class TestChunkCap:
    def test_open_ended_request_is_capped(self):
        """`bytes=0-` on a large object must not return the whole thing."""
        from daastaan_api.routers.media import _MAX_CHUNK_BYTES

        size = _MAX_CHUNK_BYTES * 3
        start, end = parse_range("bytes=0-", size)
        assert (start, end) == (0, _MAX_CHUNK_BYTES - 1)


class TestIgnoredRanges:
    """Unusable headers fall back to a normal 200 rather than failing the fetch."""

    @pytest.mark.parametrize("header", ["bytes=-", "items=0-10", "garbage", "bytes=a-b"])
    def test_returns_none(self, header):
        assert parse_range(header, SIZE) is None

    def test_empty_object(self):
        assert parse_range("bytes=0-", 0) is None


class TestUnsatisfiableRanges:
    @pytest.mark.parametrize("header", ["bytes=1000-", "bytes=1500-1600", "bytes=-0"])
    def test_raises_416(self, header):
        with pytest.raises(HTTPException) as exc:
            parse_range(header, SIZE)
        assert exc.value.status_code == 416
