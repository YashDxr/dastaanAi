# The video editor

A finished episode is one interpretation of a story: one frame shape, one caption
style, the whole thing end to end. The editor lets someone make *cuts* of it — a
wide one for YouTube, a vertical one with big captions for Reels, a thirty-second
clip of the best scene with their own music under it — and share any of them with
a link.

It is a separate studio tab on purpose. Nothing in it runs the pipeline, and a
screen full of typography controls is the wrong thing to put in front of someone
who is still waiting for their episode.

## What a cut is

A cut is a **manifest**, not a file. Saving one is a row in `video_edits`;
rendering one is a worker job. That split is what makes the editor feel
immediate: dragging a caption size slider is a `PATCH`, and the preview updates in
the browser without an encode.

The manifest lives in `daastaan_contracts.editing` because all three tiers read
it — the web editor posts it, the API stores it, the render task consumes it.

```
VideoEditManifest
├── aspect            16:9 | 9:16 | 1:1 | 4:5
├── frame_fill        crop | blur
├── trim              start_ms, end_ms
├── caption           font, size, colours, outline, box, position, wrapping…
├── caption_overrides {line_id: replacement text}
├── audio             narration/score/local gains, offset, loop, ducking, fades
├── title_card        heading, subheading, duration
├── watermark         text, corner, opacity
└── motion            ken_burns, transition_ms
```

## Why it recomposes instead of re-cutting the MP4

The obvious implementation is to take `final_video` and trim it. That does not
work here, for two reasons that are both about what is already baked into that
file:

- `compose_video` **burns the captions in**. Restyling them means painting over
  the old ones, which no font choice survives.
- The narration and the generated score are **already mixed together**. Neither
  can be adjusted, and the score cannot be dropped — which is the whole point of
  letting someone put their own track underneath.

So a render rebuilds the cut from the artifacts the pipeline already produced:
the per-line `line_audio` clips, the `scene_image` artwork, and the `music_bed`.
It costs an encode and buys every control the editor offers.

The consequence worth knowing: **the editor needs artwork and per-line audio.**
An episode generated without images has nothing to cut, and `capabilities` says
so rather than presenting controls that cannot work.

## The timeline

Both the API and the renderer need to know where each line sits in the finished
episode, to the millisecond — the API to tell the browser where the captions go,
the renderer to cut the picture. They cannot each derive it, so
`build_timeline` in `daastaan_contracts.editing` is the single implementation.

Each line occupies its spoken clip plus the pause `compose_episode` pads after
it. The picture is held for both; the caption shows for the spoken part only, so
it clears during a beat instead of hanging over silence. A line whose synthesis
failed contributes nothing at all, which keeps every later caption in step with
the audio that actually exists.

## Captions go through libass, not `drawtext`

Debian's ffmpeg is built with libass and the image already installs DejaVu, Noto
and the Indic families. One `.ass` file replaces one `drawtext` filter per line,
which for a sixty-line script is the difference between a readable filter graph
and an unreadable one. It is also the only way to get a real font choice and
per-caption timing.

The title card text and the watermark go through the same file. All three are
text over video, and a separate `drawtext` for each would be two more places for
a quoting mistake to live.

Wrapping is done in Python (`wrap_caption`) rather than left to libass, and the
browser preview reimplements the same algorithm. A caption that reflows between
the preview and the export would make the preview a suggestion rather than a
proof.

## The preview is real, except where it says it is not

The browser composites the same source material the renderer uses — the scene
artwork and the episode mix — and applies the caption style as CSS. Type,
colour, position and framing are genuinely what you get. Ken Burns and the
crossfades are not previewed, and the UI says so.

Two clocks, because a cut can open on a title card that no audio covers: during
the card the playhead runs on wall time, and after it the `<audio>` element is
the clock. That is the only way to stay in sync with a mix the browser is
decoding itself.

## Trimming

Trimming happens when segments are planned, not with an `atrim` on the finished
mix. A trim that lands mid-line has to shorten that line's **picture and the
caption over it**, which a single filter on the mix cannot express.

The UI snaps trim points to line boundaries, so a cut never begins mid-sentence
or clips a word in half. "Quick clip" buttons trim to one scene.

## Audio

The narration is rebuilt, so the score is a layer rather than something already
baked in:

- **Narration** gain, always present.
- **Score** — the generated bed. Can be dropped entirely to make room.
- **Local track** — an uploaded file, with a gain, a loop toggle, and an offset.
  The offset is the sync control: positive delays the track, negative starts it
  partway in, which is how a song's downbeat gets lined up with the first line.
- **Ducking** — sidechain compression, the same treatment `compose_episode` gives
  the score. Each bed needs its own copy of the narration, because an ffmpeg pad
  can only be consumed once.

Uploads are sniffed by magic bytes rather than trusted by extension, stored as
`local_audio` media assets, and served by the same authorised, range-requesting
code as everything the pipeline produced.

## Sharing

`POST /api/editor/cuts/{id}/share` mints a `secrets.token_urlsafe(32)` and
returns a link built from the `web_base_url` setting — from configuration rather
than from the request, so a link minted through a tunnel still points somewhere a
recipient can reach.

`/api/share/{token}` and `/api/share/{token}/video` are the **only
unauthenticated routes in the API**, so they are written to give up as little as
possible:

- Every failure is the same 404 — unknown token, expired, cut deleted, render
  missing — because distinguishing them would make the route an oracle for which
  tokens exist.
- The response describes the clip and nothing about the account behind it.
- The bytes served are the ones the cut's own render asset points at, never an
  asset id a caller can name.
- Revocation **clears** the token rather than dating it, so a withdrawn link
  cannot be reinstated by moving an expiry.
- Cache lifetimes are short and private, and responses carry
  `X-Robots-Tag: noindex`.

Sharing is refused until a render exists. A link that resolves to nothing is
worse than no link.

## Versions

A cut is a render of one particular version's footage, and its row points at that
version. When a story is regenerated:

- Uploaded backing tracks **are** carried forward. Nothing in the pipeline
  produced them, so no regeneration invalidates them, and a listener should not
  have to upload their song again because they respoke one line.
- Rendered cuts **are not**. Copying the MP4 forward would put a second row for
  the same render on a version whose script may no longer match it.

The editor shows cuts from older versions as archived: their downloads still
work, but they cannot be reopened, because their trim points at a timeline the
current episode no longer has.

## Staleness

`render_current` is a comparison of `manifest_digest(manifest)` against the
digest stored beside the render. Editing a rendered cut puts it back to `draft`
while leaving the previous MP4 in place — that file is still the last thing
actually produced, so it stays downloadable, but the editor knows it is behind
and the Export button offers work instead of a download.

## Deep links

The app routes on a hash fragment, so an editor tab is linkable:

| Fragment | Opens |
| --- | --- |
| `#/story/{id}` | the episode |
| `#/story/{id}/scenes` | the scenes panel |
| `#/story/{id}/editor` | the editor |
| `#/share/{token}` | a shared cut, no account needed |

The Episode panel shows a link to `#/story/{id}/editor` once a video exists, so
the handoff can be clicked in place or copied into the message that tells someone
their episode is done.

## Operational notes

- Renders run on the **assembly** queue, alongside the pipeline's own video work.
- Rate limited rather than budget capped: a render spends CPU on our own pool,
  not credit at a paid API, so what needs bounding is how much queue one person
  can hold (`RATE_LIMIT_RENDERS`).
- A render never touches `StoryState` or the story's status. A failed export
  leaves a story that plays perfectly well looking exactly as healthy as it is.
- The `video_edits` table is created by `SQLModel.metadata.create_all` at
  startup, like every other table here.
- Set `WEB_BASE_URL` in any environment where the web app is not on
  `http://localhost:5173`, or share links will point at the wrong origin.

## Testing

`tests/test_video_edit.py` covers the timeline arithmetic, manifest digests, trim
planning, caption wrapping, the generated ASS, and the shape of the video and
audio filter graphs. `tests/test_video_editor_api.py` covers the derived
`render_current` and `version_current` flags, share-token resolution, audio
sniffing, and version carry-over.

None of it runs ffmpeg — the graphs are asserted as text, which is faster and
localises a failure better than an encode would. What that cannot establish is
whether the graph actually *builds*, so that is worth checking by hand against a
real binary when the filter chains change.
