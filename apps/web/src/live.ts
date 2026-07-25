import type { ProgressEvent } from '@daastaan/api-types'

/**
 * The live overlay on top of polled progress.
 *
 * Events used to be parsed and thrown away: every frame just triggered a refetch of
 * `/jobs`, so the UI moved at the speed of a round trip and only ever knew what the
 * database knew. Everything the pipeline reports between stage transitions - which
 * line is being recorded, which characters have been cast - had nowhere to live.
 *
 * This is that place. `/jobs` remains the base, because it survives a reload and a
 * Redis flush; this overlay is the newer and finer-grained view of the same run, and
 * wins wherever the two describe the same stage. That ordering is safe because the
 * server replays the retained event log in order on connect, so the overlay is never
 * a partial view of something the base already knows.
 */
export type LiveProgress = {
  /** Latest status per stage, keyed by stage name. */
  stages: Record<string, string>
  /** Sub-stage counts for the fan-out stages. */
  counts: Record<string, { completed: number; total: number }>
  /** What the model has produced so far, per stage, in the order it was written. */
  previews: Record<string, { label: string; items: string[] }>
  /** Rough output-token count for a stage mid-call, for a liveness indicator. */
  tokens: Record<string, number>
  /** Set once the episode is playable. */
  completedVersionId: string | null
  /** Errors a stage reported without failing the run, such as a missing score. */
  notices: string[]
}

export const emptyLive: LiveProgress = {
  stages: {},
  counts: {},
  previews: {},
  tokens: {},
  completedVersionId: null,
  notices: [],
}

/** How many preview items to keep per stage. The script of a long story would
 *  otherwise grow without bound in memory, and nothing on screen reads that far
 *  back — the panel shows the most recent few. */
const MAX_PREVIEW_ITEMS = 60

/**
 * Fold one event into the overlay, returning a new object when something changed.
 *
 * Returns the *same* reference when an event is irrelevant or adds nothing, so React
 * can skip a render. That matters here: heartbeats and repeated token counts arrive
 * steadily and none of them should repaint the studio.
 */
export function applyEvent(state: LiveProgress, event: ProgressEvent): LiveProgress {
  switch (event.type) {
    case 'stage':
      if (state.stages[event.stage] === event.status) return state
      return { ...state, stages: { ...state.stages, [event.stage]: event.status } }

    case 'stage_progress': {
      const current = state.counts[event.stage]
      // Folded with `max` because the publishers are concurrent: a dozen TTS
      // workers increment one counter, and the order they reach the stream is not
      // the order they incremented it. Without this the count would occasionally
      // step backwards mid-fan-out.
      const completed = Math.max(current?.completed ?? 0, event.completed)
      const total = Math.max(current?.total ?? 0, event.total)
      if (current && current.completed === completed && current.total === total) return state
      return { ...state, counts: { ...state.counts, [event.stage]: { completed, total } } }
    }

    case 'stage_preview': {
      if (!event.items.length) return state
      const current = state.previews[event.stage]
      // The server sends each item once, but a reconnect can replay frames this
      // client already applied, so identical items are dropped rather than
      // duplicated on screen.
      const seen = new Set(current?.items ?? [])
      const added = event.items.filter((item) => !seen.has(item))
      if (!added.length) return state
      const items = [...(current?.items ?? []), ...added].slice(-MAX_PREVIEW_ITEMS)
      return {
        ...state,
        previews: { ...state.previews, [event.stage]: { label: event.label, items } },
      }
    }

    case 'stage_tokens': {
      if ((state.tokens[event.stage] ?? 0) >= event.tokens) return state
      return { ...state, tokens: { ...state.tokens, [event.stage]: event.tokens } }
    }

    case 'music_status':
      if (state.notices.includes(event.error)) return state
      return { ...state, notices: [...state.notices, event.error] }

    case 'complete':
      if (state.completedVersionId === event.version_id) return state
      return { ...state, completedVersionId: event.version_id }

    // Heartbeats prove the stream is alive and carry nothing. Asset and feedback
    // events change content rather than progress, so they are handled by the
    // refetch they schedule instead of being mirrored here.
    default:
      return state
  }
}

/**
 * Whether an event means the authoritative REST state is now stale.
 *
 * The overlay covers progress, but the story itself - scenes, cast, script, the
 * finished mix - lives in `state_json` and the media rows, and only a fetch can
 * produce those. Keeping the list narrow is the point: a preview or a token count
 * changes nothing on the server, and treating every frame as a reason to refetch is
 * what made the old client issue three requests per event.
 */
export function needsRefresh(event: ProgressEvent): boolean {
  switch (event.type) {
    // A finished stage has written new state; a failed one has an error worth
    // reading back. A stage merely *starting* has changed nothing yet.
    case 'stage':
      return event.status !== 'running'
    case 'asset':
    case 'complete':
    case 'feedback':
      return true
    default:
      return false
  }
}

/**
 * A story's completion as a percentage, from the coarse base and the fine overlay.
 *
 * Each planned stage is worth an equal share. A fan-out stage that is still running
 * claims its share in proportion to the lines or scenes it has finished, which is
 * what turns the several minutes of TTS from a stalled chip into a bar that moves on
 * every line.
 */
export function percentComplete(
  stages: string[],
  statusFor: (stage: string) => string,
  counts: LiveProgress['counts'],
  ready: boolean,
): number {
  if (ready) return 100
  if (!stages.length) return 0

  const earned = stages.reduce((sum, stage) => {
    const status = statusFor(stage)
    if (status === 'succeeded' || status === 'skipped') return sum + 1
    if (status !== 'running') return sum
    const count = counts[stage]
    if (!count || count.total <= 0) return sum
    return sum + Math.min(count.completed / count.total, 1)
  }, 0)

  // Floored, and capped just below completion: only a ready story shows 100, so the
  // bar cannot claim the episode is finished while assembly is still running.
  return Math.min(Math.floor((earned / stages.length) * 100), 99)
}
