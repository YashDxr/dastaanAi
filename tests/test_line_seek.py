"""Line seek math for the studio player (mirrors apps/web AudioPlayer)."""


def line_seek_seconds(assets: list[dict], lines: list[dict], line_id: str) -> float:
    by_line = {
        a["line_id"]: a
        for a in assets
        if a.get("kind") == "line_audio" and a.get("line_id")
    }
    seconds = 0.0
    for line in sorted(lines, key=lambda item: item["index"]):
        if line["id"] == line_id:
            return seconds
        clip = by_line.get(line["id"])
        seconds += (clip or {}).get("duration_ms", 0) / 1000
        seconds += line.get("pause_after_ms", 0) / 1000
    return seconds


def test_line_seek_sums_prior_clips_and_pauses():
    assets = [
        {"kind": "line_audio", "line_id": "line_0000", "duration_ms": 1000},
        {"kind": "line_audio", "line_id": "line_0001", "duration_ms": 2000},
        {"kind": "line_audio", "line_id": "line_0002", "duration_ms": 3000},
    ]
    lines = [
        {"id": "line_0000", "index": 0, "pause_after_ms": 500},
        {"id": "line_0001", "index": 1, "pause_after_ms": 0},
        {"id": "line_0002", "index": 2, "pause_after_ms": 0},
    ]
    assert line_seek_seconds(assets, lines, "line_0000") == 0
    assert line_seek_seconds(assets, lines, "line_0001") == 1.5
    assert line_seek_seconds(assets, lines, "line_0002") == 3.5
