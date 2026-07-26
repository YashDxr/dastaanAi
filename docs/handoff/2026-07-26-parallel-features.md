# Handoff: parallel feature branches started by Claude Code (2026-07-26)

**Status: resolved.** All five in-progress branches have been merged into
`dev`, the known bugs are fixed, and the web workspace builds clean. This file
is kept as the record of what changed and why, since several branches did not
land exactly as they were written.

Every branch forked from `dev` @ `20d8022` ("chore: ui fixes"). The three
already-finished branches noted at the bottom of the original handoff
(`feat/audio-sync`, `feat/character-avatars`, `feat/multilingual-ingest`) were
already in `dev` and needed no action.

---

## What landed

### `feat/streaming-thinking-copy` — merged as-is
`StreamingActivity` now shows a stage-specific line ("Casting a voice for each
character") instead of a raw token counter while a stage is running but has
nothing to show yet. No changes were needed.

### `feat/library-ux` — merged, one bug fixed
Library search, sort, filter chips with live counts, `localStorage`-backed
favorites, status dots and relative timestamps, all as described.

Fixed: the favorite star was a `<button>` rendered inside the `story-row`
`<button>`. Nested interactive elements are invalid HTML and the browser
splits the nesting, which would have broken both the row click and the star.
The star is now a sibling, absolutely positioned over the row
(`.story-row-wrap`), and `toggleFavorite` no longer needs to stop propagation.

### `feat/listener-player` — merged, transcript folded into `ScriptPanel`
The playback speed control (0.75x–2x) landed on `AudioPlayer` as written, plus
one addition: the rate is reapplied on `loadedmetadata`, because loading a new
source resets it and a rebuilt mix would silently drop back to 1x mid-episode.

`ListenerTranscript.tsx` was **not** kept. This is the duplication the original
handoff flagged against `feat/audio-sync`: `ScriptPanel` on `dev` already
highlights the active line, auto-scrolls to it, pauses auto-scroll for 4s after
a manual scroll, and seeks on click — the same four behaviours, implemented the
same way. Mounting both would have stacked two scrolling copies of the same
lines in one column.

Its one genuine addition, the scene chapter divider, moved into `ScriptPanel`,
which already receives `scenes`. That also resolves the rough edge in the
original note: the divider reads "Scene 2: The Chase" rather than a UUID
fragment. CSS moved from `.listener-chapter` to `.script-chapter`; the rest of
the `.listener-*` rules were dropped with the component.

### `feat/director-controls` — merged, entry-point bug fixed
Fixed: `SCOPE_STAGE.scene` was `emotion_tagging`, which is not in
`SCOPE_ENTRY_POINTS[Scope.SCENE]` (`packages/contracts/.../stages.py`), so
`plan_stages` would have raised on every scene-scoped regeneration. It is now
`story_understanding`, matching `branchFromScene` in `Studio.tsx`.

Because a scene therefore re-enters at the top of the pipeline and
`SCOPE_AFFECTED_STAGES[Scope.SCENE]` is the full stage list, the scene copy was
corrected to match what actually runs: the cost preview says the script,
artwork and score are all rebuilt, and the generated instruction reads "Rework
this scene with…" rather than "Deliver this scene with…", which described the
`emotion_tagging` path that never existed.

### `feat/scene-ambience` — merged, styled and wired up
`AmbiencePanel` is mounted under the scene gallery in the Scenes tab, which is
where scene-level sound design belongs, and is only rendered once scenes exist.
The missing styles (`.ambience-*`, `.sound-mode-selector`,
`.soundscape-timeline*`, `.coming-soon`) were written; timeline block widths
come from the inline `flex-basis` the component computes, so the strip stays
proportional to scene length.

The mix selector previously changed nothing when clicked. It now reports
something real: each mode gets a description of what that mix contains
(including whether a music bed has actually been generated), and the
suggestion list dims under "Narration Only", since that mix would not carry
an ambience layer. "Full Atmosphere" stays disabled — there is still no
backend for generating ambience audio, and the panel is explicitly labelled
as planning-only.

---

## Verification

`npm run build --workspace @daastaan/web` (`tsc -b && vite build`) passes.
`npm run lint --workspace @daastaan/web` reports no new errors; the single
remaining error, `rules-of-hooks` in `views/Landing.tsx:123`, predates this
work and is untouched by it.

Nothing here required a backend or contract change. Still worth a manual
smoke test: the scene scope in Director Controls (the fix above is the only
thing standing between it and a 500), the star toggle in Library, and the
Scenes tab layout with the new panel.
