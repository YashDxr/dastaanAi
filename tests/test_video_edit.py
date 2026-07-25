"""The video editor: timeline, manifest, and the filter graph it produces.

No ffmpeg here. What is worth testing is the reasoning that decides *what* to
render - where a trim lands, which segments survive it, what the subtitle file
says, and how the mix is wired - all of which is pure and none of which an encode
would tell us more clearly. `render_edit` itself is a `subprocess.run` around
these pieces.

The arithmetic matters more than it looks: a trim that lands mid-line has to
shorten the picture and the caption over it, and the browser preview computes the
same numbers independently. Any drift between the two makes the preview a
suggestion rather than a proof.
"""

import json

import pytest
from daastaan_agent.video_edit import (
    AudioSources,
    Segment,
    VideoEditError,
    _apply_fades,
    _ass_color,
    _ass_escape,
    _ass_time,
    _audio_filters,
    _escape_filter_path,
    _has_burned_text,
    _segment_video_filters,
    _xfade_chain,
    build_ass,
    plan_segments,
    wrap_caption,
)
from daastaan_contracts import (
    ASPECT_SIZES,
    CAPTION_PRESETS,
    AspectRatio,
    AudioMix,
    CaptionPosition,
    CaptionStyle,
    Corner,
    DialogueLine,
    FrameFill,
    LineType,
    Motion,
    TitleCard,
    TrimRange,
    VideoEditManifest,
    Watermark,
    build_timeline,
    manifest_digest,
    timeline_duration_ms,
)
from pydantic import ValidationError

# --- helpers ---------------------------------------------------------------


def line(
    index: int, *, scene: str = "scene_00", pause: int = 500, text: str = "Hello."
) -> DialogueLine:
    return DialogueLine(
        id=f"line_{index:04d}",
        scene_id=scene,
        index=index,
        speaker="Lily",
        character_id="char_01",
        text=text,
        line_type=LineType.DIALOGUE,
        pause_after_ms=pause,
    )


def spans_of(count: int, *, audio_ms: int = 1000, pause: int = 500):
    lines = [line(index, pause=pause) for index in range(count)]
    return build_timeline(lines, {item.id: audio_ms for item in lines})


def segment(duration_ms: int, *, caption: str = "", caption_ms: int | None = None) -> Segment:
    return Segment(
        image=b"\x89PNG",
        duration_ms=duration_ms,
        caption=caption,
        caption_ms=duration_ms if caption_ms is None else caption_ms,
    )


def ass_style(script: str, name: str) -> dict[str, str]:
    """One `Style:` row as a mapping, read against the script's own `Format:` row.

    Positional indexing into a twenty-three field line is unreadable and silently
    wrong the moment a field moves, which is exactly the mistake this catches.
    """
    rows = script.splitlines()
    columns = [
        field.strip()
        for field in next(row for row in rows if row.startswith("Format: Name,"))
        .removeprefix("Format:")
        .split(",")
    ]
    values = next(row for row in rows if row.startswith(f"Style: {name},")).removeprefix(
        "Style:"
    ).split(",")
    return dict(zip(columns, [value.strip() for value in values], strict=True))


# --- timeline --------------------------------------------------------------


def test_timeline_lays_lines_out_back_to_back_including_pauses():
    spans = spans_of(3, audio_ms=1000, pause=500)

    assert [span.start_ms for span in spans] == [0, 1500, 3000]
    assert [span.caption_end_ms for span in spans] == [1000, 2500, 4000]
    assert timeline_duration_ms(spans) == 4500


def test_timeline_skips_lines_with_no_audio_without_shifting_the_rest():
    """A line whose synthesis failed contributes nothing.

    `compose_episode` omits it from the mix, so counting its pause here would put
    every later caption out of step with the audio that actually exists.
    """
    lines = [line(0), line(1), line(2)]
    spans = build_timeline(lines, {"line_0000": 1000, "line_0002": 2000})

    assert [span.line_id for span in spans] == ["line_0000", "line_0002"]
    assert [span.start_ms for span in spans] == [0, 1500]
    assert timeline_duration_ms(spans) == 4000


def test_timeline_orders_by_line_index_not_by_argument_order():
    lines = [line(2), line(0), line(1)]
    spans = build_timeline(lines, {item.id: 1000 for item in lines})

    assert [span.index for span in spans] == [0, 1, 2]


def test_timeline_of_nothing_is_empty_rather_than_an_error():
    assert build_timeline([], {}) == []
    assert timeline_duration_ms([]) == 0


# --- manifest --------------------------------------------------------------


def test_a_default_manifest_is_valid_and_needs_no_duration():
    manifest = VideoEditManifest()

    assert manifest.aspect is AspectRatio.LANDSCAPE
    assert manifest.trim.start_ms == 0
    # The editor opens a draft before it knows how long the episode is.
    assert manifest.trim.end_ms is None


def test_manifest_digest_ignores_key_order():
    manifest = VideoEditManifest()
    reordered = VideoEditManifest.model_validate(
        dict(reversed(list(manifest.model_dump(mode="json").items())))
    )

    assert manifest_digest(manifest) == manifest_digest(reordered)


def test_manifest_digest_survives_a_json_round_trip():
    """The digest is compared against one stored on a row that came back from
    Postgres, so it has to be stable across serialisation."""
    manifest = VideoEditManifest(caption=CAPTION_PRESETS["bold_social"])
    stored = json.loads(json.dumps(manifest.model_dump(mode="json")))

    assert manifest_digest(VideoEditManifest.model_validate(stored)) == manifest_digest(manifest)


@pytest.mark.parametrize(
    "change",
    [
        {"aspect": AspectRatio.PORTRAIT},
        {"frame_fill": FrameFill.BLUR},
        {"trim": TrimRange(start_ms=1000)},
        {"caption": CaptionStyle(size_pt=44)},
        {"caption_overrides": {"line_0000": "Different."}},
        {"audio": AudioMix(local_gain_db=-6.0)},
        {"title_card": TitleCard(enabled=True)},
        {"watermark": Watermark(enabled=True, text="@me")},
        {"motion": Motion(ken_burns=False)},
    ],
)
def test_every_visible_change_changes_the_digest(change):
    """`render_current` is this comparison and nothing else, so a change the digest
    misses would leave the editor offering a stale MP4 as the current cut."""
    assert manifest_digest(VideoEditManifest(**change)) != manifest_digest(VideoEditManifest())


def test_trim_end_must_come_after_its_start():
    with pytest.raises(ValidationError):
        TrimRange(start_ms=5000, end_ms=5000)
    with pytest.raises(ValidationError):
        TrimRange(start_ms=5000, end_ms=1000)


def test_manifest_rejects_a_colour_that_is_not_a_hex_triple():
    """Colours reach an ASS style line, so the pattern is the guarantee that a
    value getting that far cannot carry syntax."""
    for bad in ("red", "#FFF", "#12345g", "#FFFFFF;x", ""):
        with pytest.raises(ValidationError):
            CaptionStyle(primary_color=bad)


def test_manifest_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        VideoEditManifest.model_validate({"aspect": "16:9", "surprise": True})


def test_caption_overrides_are_bounded():
    with pytest.raises(ValidationError):
        VideoEditManifest(caption_overrides={"line_0000": "x" * 5000})
    with pytest.raises(ValidationError):
        VideoEditManifest(caption_overrides={"x" * 200: "fine"})


def test_every_aspect_has_even_dimensions():
    """yuv420p subsamples by two and libx264 refuses an odd dimension, so an odd
    preset here would fail at the very end of a long encode."""
    for aspect in AspectRatio:
        width, height = ASPECT_SIZES[aspect]
        assert width % 2 == 0 and height % 2 == 0


# --- segments and trimming -------------------------------------------------


def test_segments_follow_the_timeline_when_nothing_is_trimmed():
    spans = spans_of(3)
    images = {span.line_id: b"png" for span in spans}

    segments = plan_segments(spans, images, VideoEditManifest())

    assert [item.duration_ms for item in segments] == [1500, 1500, 1500]
    assert [item.caption_ms for item in segments] == [1000, 1000, 1000]


def test_a_trim_mid_line_shortens_that_segment_and_its_caption():
    """The reason trimming is done here rather than with one `atrim` on the mix:
    a partial line needs its picture and its caption clipped together."""
    spans = spans_of(3)
    images = {span.line_id: b"png" for span in spans}
    # Keep from 500ms into the first line to 2000ms, which is 500ms into the
    # second line's spoken part.
    manifest = VideoEditManifest(trim=TrimRange(start_ms=500, end_ms=2000))

    segments = plan_segments(spans, images, manifest)

    assert [item.duration_ms for item in segments] == [1000, 500]
    assert [item.caption_ms for item in segments] == [500, 500]
    assert sum(item.duration_ms for item in segments) == 1500


def test_a_trim_landing_in_a_pause_keeps_the_picture_and_drops_no_caption_time():
    spans = spans_of(2)
    images = {span.line_id: b"png" for span in spans}
    # 1200ms is inside the first line's trailing 500ms pause.
    manifest = VideoEditManifest(trim=TrimRange(end_ms=1200))

    segments = plan_segments(spans, images, manifest)

    assert len(segments) == 1
    assert segments[0].duration_ms == 1200
    # The whole spoken part survived, so the caption is not shortened.
    assert segments[0].caption_ms == 1000


def test_lines_outside_the_trim_are_dropped_entirely():
    spans = spans_of(4)
    images = {span.line_id: b"png" for span in spans}
    manifest = VideoEditManifest(trim=TrimRange(start_ms=3000, end_ms=4500))

    segments = plan_segments(spans, images, manifest)

    assert len(segments) == 1
    assert segments[0].duration_ms == 1500


def test_a_line_with_no_artwork_is_skipped_rather_than_rendered_black():
    spans = spans_of(3)
    images = {spans[0].line_id: b"png", spans[2].line_id: b"png"}

    segments = plan_segments(spans, images, VideoEditManifest())

    assert len(segments) == 2


def test_artwork_falls_back_to_the_scene_when_there_is_none_per_line():
    """The pipeline makes one image per scene in some configurations and one per
    line in others, and the editor must work with either."""
    spans = spans_of(2)
    segments = plan_segments(spans, {"scene_00": b"png"}, VideoEditManifest())

    assert len(segments) == 2


def test_a_trim_that_keeps_no_artwork_is_refused():
    spans = spans_of(2)
    with pytest.raises(VideoEditError, match="artwork"):
        plan_segments(spans, {}, VideoEditManifest())


def test_an_empty_trim_window_is_refused():
    """`TrimRange` cannot express this, but a manifest saved against a longer
    episode can outlive the version it was made for."""
    spans = spans_of(1)
    manifest = VideoEditManifest.model_construct(
        **{**VideoEditManifest().model_dump(), "trim": TrimRange(start_ms=9_000)}
    )
    with pytest.raises(VideoEditError, match="empty"):
        plan_segments(spans, {"line_0000": b"png"}, manifest)


def test_a_title_card_becomes_a_leading_segment_with_no_image():
    spans = spans_of(1)
    manifest = VideoEditManifest(title_card=TitleCard(enabled=True, duration_ms=2000))

    segments = plan_segments(spans, {"line_0000": b"png"}, manifest)

    assert segments[0].image is None
    assert segments[0].duration_ms == 2000
    assert segments[0].caption_ms == 0
    assert segments[1].image is not None


# --- captions --------------------------------------------------------------


def test_captions_wrap_at_word_boundaries():
    wrapped = wrap_caption("the quick brown fox jumps over the lazy dog", 16)

    assert wrapped.split("\n") == ["the quick brown", "fox jumps over", "the lazy dog"]
    assert all(len(part) <= 16 for part in wrapped.split("\n"))


def test_a_word_longer_than_the_line_gets_its_own_line_rather_than_being_cut():
    wrapped = wrap_caption("a supercalifragilistic b", 10)

    assert wrapped.split("\n") == ["a", "supercalifragilistic", "b"]


def test_the_speaker_prefix_and_capitals_apply_to_the_caption():
    spans = spans_of(1)
    manifest = VideoEditManifest(
        caption=CaptionStyle(show_speaker=True, uppercase=True, max_chars_per_line=80)
    )

    segments = plan_segments(spans, {"line_0000": b"png"}, manifest)

    assert segments[0].caption == "LILY: HELLO."


def test_a_caption_override_replaces_the_scripted_line():
    spans = spans_of(1)
    manifest = VideoEditManifest(
        caption=CaptionStyle(show_speaker=False, max_chars_per_line=80),
        caption_overrides={"line_0000": "Something better."},
    )

    segments = plan_segments(spans, {"line_0000": b"png"}, manifest)

    assert segments[0].caption == "Something better."


def test_turning_captions_off_leaves_the_segments_but_empties_the_text():
    spans = spans_of(2)
    manifest = VideoEditManifest(caption=CaptionStyle(enabled=False))

    segments = plan_segments(spans, {span.line_id: b"png" for span in spans}, manifest)

    assert len(segments) == 2
    assert all(item.caption == "" for item in segments)


# --- ASS -------------------------------------------------------------------


def test_ass_colours_are_reordered_and_alpha_inverted():
    """ASS is `&HAABBGGRR` and treats alpha as transparency, so opaque is zero."""
    assert _ass_color("#FF8000") == "&H000080FF"
    assert _ass_color("#FFFFFF", 255) == "&HFFFFFFFF"


def test_ass_timestamps_are_centiseconds():
    assert _ass_time(0) == "0:00:00.00"
    assert _ass_time(1_234) == "0:00:01.23"
    assert _ass_time(3_661_000) == "1:01:01.00"
    assert _ass_time(-50) == "0:00:00.00"


def test_ass_escaping_neutralises_override_blocks():
    """A brace in a caption would otherwise open an override block and let model
    output reposition or recolour itself."""
    escaped = _ass_escape("{\\pos(0,0)}drop")

    assert "{" not in escaped and "}" not in escaped
    assert "drop" in escaped


def test_ass_escaping_turns_newlines_into_hard_breaks_and_drops_controls():
    assert _ass_escape("one\ntwo") == "one\\Ntwo"
    assert _ass_escape("a\x00\x07b") == "ab"


def test_ass_events_run_consecutively_across_segments():
    segments = [
        segment(1500, caption="One", caption_ms=1000),
        segment(1500, caption="Two", caption_ms=1000),
    ]

    script = build_ass(segments, VideoEditManifest(), width=1280, height=720, total_ms=3000)
    events = [row for row in script.splitlines() if row.startswith("Dialogue:")]

    assert len(events) == 2
    assert "0:00:00.00,0:00:01.00" in events[0]
    # Second caption starts when the first segment ends, not when its caption did:
    # the picture is held through the pause, the caption is not.
    assert "0:00:01.50,0:00:02.50" in events[1]


def test_a_segment_with_no_caption_produces_no_event():
    segments = [
        segment(1000, caption="", caption_ms=0),
        segment(1000, caption="Here", caption_ms=1000),
    ]

    script = build_ass(segments, VideoEditManifest(), width=1280, height=720, total_ms=2000)
    events = [row for row in script.splitlines() if row.startswith("Dialogue:")]

    assert len(events) == 1
    assert "0:00:01.00,0:00:02.00" in events[0]


def test_play_resolution_matches_the_output_so_sizes_mean_pixels():
    script = build_ass(
        [segment(1000)], VideoEditManifest(aspect=AspectRatio.PORTRAIT),
        width=720, height=1280, total_ms=1000,
    )

    assert "PlayResX: 720" in script
    assert "PlayResY: 1280" in script


def test_wrapping_is_disabled_in_the_script_because_we_already_wrapped():
    script = build_ass([segment(1000)], VideoEditManifest(), width=1280, height=720, total_ms=1000)

    assert "WrapStyle: 2" in script


def test_a_box_uses_border_style_three_and_carries_its_opacity():
    style = CaptionStyle(box=True, box_opacity=0.5, outline_color="#000000")
    script = build_ass(
        [segment(1000)], VideoEditManifest(caption=style), width=1280, height=720, total_ms=1000
    )
    fields = ass_style(script, "Caption")

    assert fields["BorderStyle"] == "3"
    # BackColour is the outline colour at the requested opacity, inverted.
    assert fields["BackColour"] == f"&H{round(0.5 * 255):02X}000000".upper()


def test_no_box_uses_outline_and_shadow_instead():
    script = build_ass(
        [segment(1000)], VideoEditManifest(caption=CaptionStyle(box=False)),
        width=1280, height=720, total_ms=1000,
    )

    assert ass_style(script, "Caption")["BorderStyle"] == "1"


@pytest.mark.parametrize(
    ("position", "alignment"),
    [(CaptionPosition.TOP, "8"), (CaptionPosition.MIDDLE, "5"), (CaptionPosition.BOTTOM, "2")],
)
def test_caption_position_maps_to_ass_alignment(position, alignment):
    script = build_ass(
        [segment(1000)], VideoEditManifest(caption=CaptionStyle(position=position)),
        width=1280, height=720, total_ms=1000,
    )

    assert ass_style(script, "Caption")["Alignment"] == alignment


@pytest.mark.parametrize(
    ("corner", "alignment"),
    [
        (Corner.TOP_LEFT, "7"),
        (Corner.TOP_RIGHT, "9"),
        (Corner.BOTTOM_LEFT, "1"),
        (Corner.BOTTOM_RIGHT, "3"),
    ],
)
def test_watermark_corner_maps_to_ass_alignment(corner, alignment):
    script = build_ass(
        [segment(1000)],
        VideoEditManifest(watermark=Watermark(enabled=True, text="@me", position=corner)),
        width=1280, height=720, total_ms=1000,
    )

    assert ass_style(script, "Watermark")["Alignment"] == alignment


def test_a_watermark_is_held_for_the_whole_cut():
    script = build_ass(
        [segment(1000)],
        VideoEditManifest(watermark=Watermark(enabled=True, text="@me")),
        width=1280, height=720, total_ms=42_000,
    )
    event = next(row for row in script.splitlines() if ",Watermark," in row)

    assert "0:00:00.00,0:00:42.00" in event


def test_title_card_text_appears_only_for_the_card():
    card = TitleCard(enabled=True, heading="A Story", subheading="by someone", duration_ms=2000)
    script = build_ass(
        [Segment(image=None, duration_ms=2000, caption="", caption_ms=0)],
        VideoEditManifest(title_card=card),
        width=1280, height=720, total_ms=2000,
    )

    heading = next(row for row in script.splitlines() if ",Heading," in row)
    assert "0:00:00.00,0:00:02.00" in heading
    assert "A Story" in heading
    assert any(",Subheading," in row for row in script.splitlines())


def test_an_empty_heading_produces_no_event():
    card = TitleCard(enabled=True, heading="   ", subheading="", duration_ms=2000)
    script = build_ass(
        [segment(1000)], VideoEditManifest(title_card=card),
        width=1280, height=720, total_ms=1000,
    )

    assert not any(",Heading," in row for row in script.splitlines())


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (VideoEditManifest(), True),
        (VideoEditManifest(caption=CaptionStyle(enabled=False)), False),
        (
            VideoEditManifest(
                caption=CaptionStyle(enabled=False),
                watermark=Watermark(enabled=True, text="@me"),
            ),
            True,
        ),
        (
            # Enabled but blank: nothing to draw, so the subtitle pass is skipped.
            VideoEditManifest(
                caption=CaptionStyle(enabled=False), watermark=Watermark(enabled=True, text="  ")
            ),
            False,
        ),
        (
            VideoEditManifest(
                caption=CaptionStyle(enabled=False),
                title_card=TitleCard(enabled=True, heading="Hi"),
            ),
            True,
        ),
    ],
)
def test_the_subtitle_pass_is_only_added_when_there_is_text_to_burn(manifest, expected):
    assert _has_burned_text(manifest) is expected


# --- video graph -----------------------------------------------------------


def test_every_segment_chain_ends_on_its_own_label_at_the_output_size():
    """`xfade` refuses to join streams that differ in size, pixel format, SAR or
    frame rate, which is the usual reason a graph like this fails to build."""
    segments = [segment(1000), segment(1000), segment(1000)]

    filters, labels = _segment_video_filters(
        segments, VideoEditManifest(), width=1280, height=720
    )

    assert labels == ["seg0", "seg1", "seg2"]
    for index in range(3):
        chain = next(item for item in filters if item.endswith(f"[seg{index}]"))
        assert "s=1280x720" in chain
        assert "setsar=1" in chain
        assert "format=yuv420p" in chain
        assert f"fps={25}" in chain


def test_a_still_is_held_by_zoompan_even_with_motion_off():
    """`zoompan` emits a known number of frames from one input frame. Looping the
    input instead would produce a segment that never ends."""
    filters, _ = _segment_video_filters(
        [segment(2000)], VideoEditManifest(motion=Motion(ken_burns=False)),
        width=1280, height=720,
    )
    chain = next(item for item in filters if "zoompan" in item)

    assert "z='1'" in chain
    assert "d=50" in chain  # 2000ms at 25fps


def test_ken_burns_varies_the_move_between_consecutive_segments():
    segments = [segment(1000) for _ in range(4)]
    filters, _ = _segment_video_filters(
        segments, VideoEditManifest(motion=Motion(ken_burns=True)), width=1280, height=720
    )
    moves = [item for item in filters if "zoompan" in item]

    assert len({item.split(":d=")[0] for item in moves}) == 4


def test_every_segment_but_the_last_carries_the_crossfade_overlap():
    """`xfade` consumes material from both sides of a join, so a segment that is
    exactly its own length leaves the transition nothing to work with."""
    segments = [segment(1000), segment(1000)]
    manifest = VideoEditManifest(motion=Motion(transition_ms=500))

    filters, _ = _segment_video_filters(segments, manifest, width=1280, height=720)
    frames = [int(item.split(":d=")[1].split(":")[0]) for item in filters if "zoompan" in item]

    assert frames == [round(1.5 * 25), round(1.0 * 25)]


def test_the_title_card_source_needs_no_scaling():
    card = Segment(image=None, duration_ms=2000, caption="", caption_ms=0)

    filters, labels = _segment_video_filters(
        [card, segment(1000)], VideoEditManifest(), width=1280, height=720
    )

    assert filters[0] == "[0:v]setsar=1,format=yuv420p[seg0]"
    assert labels[0] == "seg0"


def test_blur_fill_keeps_the_whole_picture_over_a_blurred_copy():
    filters, _ = _segment_video_filters(
        [segment(1000)], VideoEditManifest(frame_fill=FrameFill.BLUR), width=720, height=1280
    )
    fit = next(item for item in filters if "gblur" in item)

    assert "force_original_aspect_ratio=increase" in fit  # backdrop fills
    assert "force_original_aspect_ratio=decrease" in fit  # picture fits
    assert "overlay=(W-w)/2:(H-h)/2" in fit


def test_crop_fill_scales_up_and_crops():
    filters, _ = _segment_video_filters(
        [segment(1000)], VideoEditManifest(frame_fill=FrameFill.CROP), width=720, height=1280
    )
    fit = next(item for item in filters if item.startswith("[0:v]scale"))

    assert "force_original_aspect_ratio=increase" in fit
    assert "crop=720:1280" in fit
    assert "gblur" not in fit


def test_one_segment_needs_no_join():
    filters, label = _xfade_chain([segment(1000)], ["seg0"], VideoEditManifest())

    assert filters == []
    assert label == "seg0"


def test_crossfade_offsets_accumulate_over_segment_durations():
    segments = [segment(1000), segment(2000), segment(1500)]
    manifest = VideoEditManifest(motion=Motion(transition_ms=500))

    filters, label = _xfade_chain(segments, ["seg0", "seg1", "seg2"], manifest)

    assert label == "vjoined"
    assert "offset=1.000" in filters[0]
    assert "offset=3.000" in filters[1]
    assert all("duration=0.500" in item for item in filters)


def test_a_hard_cut_concatenates_instead_of_crossfading():
    segments = [segment(1000), segment(1000)]
    manifest = VideoEditManifest(motion=Motion(transition_ms=0))

    filters, label = _xfade_chain(segments, ["seg0", "seg1"], manifest)

    assert label == "vjoined"
    assert "concat=n=2:v=1:a=0" in filters[0]
    assert "xfade" not in filters[0]


def test_filter_path_escaping_covers_every_separator():
    escaped = _escape_filter_path("a:b[c],d;e'f")

    for char in (":", "[", "]", ",", ";", "'"):
        assert f"\\{char}" in escaped


# --- audio graph -----------------------------------------------------------


def audio_graph(manifest: VideoEditManifest, sources: AudioSources, **kwargs):
    defaults = {
        "narration_input": 0,
        "score_input": None,
        "local_input": None,
        "trim_start_ms": 0,
        "trim_end_ms": 10_000,
        "lead_in_ms": 0,
        "total_ms": 10_000,
    }
    return _audio_filters(manifest, sources, **{**defaults, **kwargs})


def test_narration_alone_is_trimmed_normalised_and_needs_no_mix():
    filters, label = audio_graph(VideoEditManifest(), AudioSources(narration=b"mp3"))

    joined = ";".join(filters)
    assert "atrim=start=0.000:end=10.000" in joined
    assert "loudnorm" in joined
    assert "amix" not in joined
    assert label == "mixraw"


def test_a_title_card_delays_the_narration_by_its_length():
    """Without this the first line would speak over the card."""
    filters, _ = audio_graph(
        VideoEditManifest(title_card=TitleCard(enabled=True, duration_ms=2000)),
        AudioSources(narration=b"mp3"),
        lead_in_ms=2000,
        total_ms=12_000,
    )

    assert "adelay=delays=2000:all=1" in filters[0]


def test_narration_is_padded_and_trimmed_to_the_length_of_the_cut():
    """The picture runs a transition longer than the mix, so the audio is padded
    rather than the video being cut back to it."""
    filters, _ = audio_graph(VideoEditManifest(), AudioSources(narration=b"mp3"), total_ms=11_500)

    assert "apad,atrim=end=11.500" in filters[0]


def test_the_score_is_mixed_in_and_dropped_when_asked():
    kept, _ = audio_graph(
        VideoEditManifest(), AudioSources(narration=b"mp3", score=b"wav"), score_input=1
    )
    assert any("[score]" in item for item in kept)

    dropped, _ = audio_graph(
        VideoEditManifest(audio=AudioMix(keep_score=False)),
        AudioSources(narration=b"mp3", score=b"wav"),
        score_input=1,
    )
    assert not any("[score]" in item for item in dropped)
    assert not any("amix" in item for item in dropped)


def test_a_positive_local_offset_delays_the_track():
    filters, _ = audio_graph(
        VideoEditManifest(audio=AudioMix(local_offset_ms=3000)),
        AudioSources(narration=b"mp3", local=b"mp3"),
        local_input=1,
    )
    local = next(item for item in filters if item.endswith("[local]"))

    assert "adelay=delays=3000:all=1" in local
    assert "atrim=start=" not in local.split(",volume")[0]


def test_a_negative_local_offset_starts_the_track_partway_in():
    """How a listener skips a song's intro to land its downbeat on the first line."""
    filters, _ = audio_graph(
        VideoEditManifest(audio=AudioMix(local_offset_ms=-4500)),
        AudioSources(narration=b"mp3", local=b"mp3"),
        local_input=1,
    )
    local = next(item for item in filters if item.endswith("[local]"))

    assert "atrim=start=4.500" in local
    assert "adelay" not in local


def test_ducking_splits_the_narration_once_per_bed():
    """An ffmpeg pad can only be consumed once, so each sidechain needs its own
    copy plus one for the mix itself."""
    filters, _ = audio_graph(
        VideoEditManifest(audio=AudioMix(duck_under_narration=True)),
        AudioSources(narration=b"mp3", score=b"wav", local=b"mp3"),
        score_input=1,
        local_input=2,
    )
    split = next(item for item in filters if "asplit" in item)

    assert "asplit=3" in split
    assert split.count("[narrsc") == 2
    assert sum("sidechaincompress" in item for item in filters) == 2


def test_without_ducking_the_beds_go_straight_into_the_mix():
    filters, _ = audio_graph(
        VideoEditManifest(audio=AudioMix(duck_under_narration=False)),
        AudioSources(narration=b"mp3", score=b"wav"),
        score_input=1,
    )

    assert not any("sidechaincompress" in item for item in filters)
    assert "[narr][score]amix=inputs=2" in next(item for item in filters if "amix" in item)


def test_the_mix_measures_its_length_against_the_narration():
    """`duration=first` with narration first is what stops a looping backing track
    running past the picture."""
    filters, _ = audio_graph(
        VideoEditManifest(), AudioSources(narration=b"mp3", local=b"mp3"), local_input=1
    )
    mix = next(item for item in filters if "amix" in item)

    assert "duration=first" in mix
    assert "normalize=0" in mix
    assert mix.index("narr") < mix.index("local")


def test_gains_are_only_written_when_they_are_not_unity():
    quiet, _ = audio_graph(
        VideoEditManifest(audio=AudioMix(narration_gain_db=-6.0)), AudioSources(narration=b"mp3")
    )
    assert "volume=-6.00dB" in quiet[0]

    flat, _ = audio_graph(VideoEditManifest(), AudioSources(narration=b"mp3"))
    assert "volume=" not in flat[0]


def test_fades_are_appended_and_the_out_starts_before_the_end():
    filters, label = _apply_fades(
        [], "mixnorm",
        VideoEditManifest(audio=AudioMix(fade_in_ms=1000, fade_out_ms=2000)),
        10_000,
    )

    assert label == "aout"
    assert "afade=t=in:st=0:d=1.000" in filters[0]
    assert "afade=t=out:st=8.000:d=2.000" in filters[0]


def test_no_fades_leaves_the_chain_and_its_label_alone():
    filters, label = _apply_fades([], "mixnorm", VideoEditManifest(), 10_000)

    assert filters == []
    assert label == "mixnorm"


def test_a_fade_out_longer_than_the_cut_starts_at_zero_rather_than_going_negative():
    filters, _ = _apply_fades(
        [], "mixnorm", VideoEditManifest(audio=AudioMix(fade_out_ms=9000)), 3000
    )

    assert "st=0.000" in filters[0]
