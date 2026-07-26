# Handoff: parallel feature branches started by Claude Code (2026-07-26)

Claude Code was running several features in parallel (one git worktree per
feature) when it hit a usage limit. All in-progress work has been committed
and pushed to `origin` as separate branches so it can be picked up on any
machine. Nothing has been merged into `dev`.

## How to resume on another machine

```bash
git fetch origin
git worktree add ../worktrees/<name> <branch>   # or just `git checkout <branch>` in a fresh clone
cd apps/web && npm install
```

Each branch below is self-contained and branches from `dev` @ `20d8022`
("chore: ui fixes"), so diffing against `dev` shows exactly what each one adds.

---

### `feat/director-controls`
Commit: `6c315b5` — "feat: add director controls for regenerating scenes/lines with tone and mood adjustments"

New `apps/web/src/components/DirectorControls.tsx`, wired into `Studio.tsx`
below the existing player/video panels. Lets a user pick a scope (line /
character / scene / music / whole episode), a target, and tone sliders or
mood/tone chips, builds a natural-language `instruction_delta`, and calls the
existing `storiesApi.regenerate()` endpoint (same one `FeedbackComposer`,
`AlternateEndings`, and `respeakLine`/`branchFromScene` in `Studio.tsx`
already use).

**Known bug to fix before this is usable:** the `SCOPE_STAGE` map sends the
wrong `target_stage` for the `scene` scope:

```ts
const SCOPE_STAGE: Record<Scope, string> = {
  line: 'tts_synthesis',
  character: 'voice_assignment',
  scene: 'emotion_tagging',      // <- invalid entry point, will raise on the backend
  music: 'music_generation',
  full_story: 'story_understanding',
}
```

Per `packages/contracts/src/daastaan_contracts/stages.py`
(`SCOPE_ENTRY_POINTS`), `Scope.SCENE` only accepts `IMAGE_GENERATION` or
`STORY_UNDERSTANDING` as an entry stage — not `EMOTION_TAGGING`. Sending
`emotion_tagging` for a `scene` scope will fail `is_valid_entry()` and
raise a `ValueError` on the backend (`plan_stages`). Fix by changing that
line to `scene: 'story_understanding'` (matches what `branchFromScene`
already does in `Studio.tsx`), or `'image_generation'` if the intent is a
lighter-weight re-render.

Otherwise CSS-only additions in `App.css` (`.director-controls`, `.dc-*`),
no backend changes needed — the API already supports this shape.

---

### `feat/library-ux`
Commit: `5fe7ba2` — "feat: add library search, status filters, sorting, and favorites"

Rewrites the toolbar of `apps/web/src/views/Library.tsx`: free-text search
over titles, a sort dropdown (newest/oldest/A–Z), filter chips (All / Ready /
In Progress / Needs Attention / Favorites) with live counts, a star-toggle
"favorite" button per story persisted to `localStorage` under
`daastaan:favorites`, status dots, and relative timestamps
("3 days ago") replacing the old absolute date.

This one looks feature-complete and frontend-only — no backend or type
changes required. Good candidate to review/merge as-is after a smoke test.

---

### `feat/listener-player`
Commit: `673056e` — "feat: add listener transcript with synced highlighting and playback speed control"

Two independent additions:
1. **Playback speed control** in `apps/web/src/components/AudioPlayer.tsx`:
   buttons for 0.75x/1x/1.25x/1.5x/2x, sets `audio.playbackRate`.
2. **New `ListenerTranscript.tsx`** component wired into `Studio.tsx`: shows
   the full script, highlights the currently-playing line
   (`activeLineId` prop), auto-scrolls to it, pauses auto-scroll for 4s after
   manual scrolling, and lets you click a line to seek
   (`onSeekToLine` → `setSeekLineId` in `Studio.tsx`).

**Rough edge to polish:** the scene "chapter" divider currently renders the
raw scene UUID (`Scene: {line.scene_id.slice(0, 8)}`) instead of the scene's
actual title. `Studio.tsx`/`StoryState` already has `state.scenes` with a
`title` field — build a `sceneId -> title` lookup and pass it in, or pass
`scenes` as a prop, so the divider reads e.g. "Scene 2: The Chase" instead of
a hex fragment.

---

### `feat/scene-ambience`
Commit: `7ac24be` — "feat: add ambience panel with scene-based sound design suggestions"

New `apps/web/src/components/AmbiencePanel.tsx` only — **not yet imported
anywhere**. It takes `scenes`, `lines`, `hasMusicBed` props and:
- suggests an ambience type per scene from regex matches against
  `scene.setting` / `scene.mood_tag` (ocean, forest, rain, city, cave, etc.)
- renders a proportional "soundscape timeline" strip across all scenes
- offers a `Narration Only` / `Narration + Score` / `Full Atmosphere`
  mode selector (the third is marked `disabled` with a "Coming soon" note —
  there's no backend support for actual ambience audio generation yet, this
  is presentation-only/for planning)

**Next step:** wire `<AmbiencePanel scenes={state.scenes} lines={state.lines}
hasMusicBed={...} />` into `Studio.tsx` (no existing CSS classes for
`.ambience-panel`, `.sound-mode-selector`, `.soundscape-timeline*`,
`.ambience-scene*`, `.coming-soon` were added — those need to be written in
`App.css` before this will look right, unlike the other three branches which
already included their CSS).

---

### `feat/streaming-thinking-copy`
Commit: `2bcdaae` — "feat: show stage-specific thinking copy during streaming activity"

Small, self-contained change to `apps/web/src/components/StreamingActivity.tsx`.
Previously, while a pipeline stage was running but had no visible items yet,
the UI just showed a live token counter ("1,234 tokens written"). This adds a
`THINKING_COPY` map with a human-readable line per stage (e.g.
"Casting a voice for each character" for `voice_assignment`) shown instead.
This one was sitting as an uncommitted change directly on `dev` (not in its
own worktree) — branched off before it could be lost. Straightforward to
review and merge.

---

## Already finished before the limit hit (no action needed)

These were already fully committed and pushed by Claude Code — clean, no
local changes were sitting around for them:

- `feat/audio-sync` (`330a387`) — real-time audio-to-script sync with
  highlighting and auto-scroll (overlaps conceptually with
  `feat/listener-player`'s transcript — check for merge conflicts /
  duplicated intent if landing both).
- `feat/character-avatars` (`77a865a`) — character avatars with
  role-based colors, integrated into the script view.
- `feat/multilingual-ingest` (`945e188`) — propagates the language chosen in
  Compose through the ingest pipeline into OCR.

## Suggested order to land these

1. `feat/streaming-thinking-copy` and `feat/library-ux` — smallest, no known
   bugs, frontend-only.
2. `feat/listener-player` — fix the scene-title divider, then merge. Compare
   with `feat/audio-sync` first since both touch line-highlighting/transcript
   territory.
3. `feat/director-controls` — fix the `scene` → `emotion_tagging` bug above
   before merging or even testing manually, otherwise the scene-scope option
   will 500.
4. `feat/scene-ambience` — needs its CSS written and to be wired into
   `Studio.tsx` before it does anything visible; treat as the least finished
   of the five.

None of these branches were build/type-checked before the limit was hit —
run `npm run build --workspace @daastaan/web` (or `npm run lint`) on each
before opening a PR.
