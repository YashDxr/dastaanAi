/**
 * Live "AI at work" panel shown while a stage is generating.
 *
 * Three visual layers:
 *   1. An animated SVG orb (always): pulsing core + expanding ripple rings +
 *      an orbiting spark. Same visual regardless of stage.
 *   2. A stage-specific accent below the orb: equalizer bars for TTS (audio),
 *      blinking tiles for image generation.
 *   3. A content column: the label, preview items materialising with a
 *      typewriter cursor, or a token count for stages with nothing richer to show,
 *      plus a thin progress bar for fan-out stages.
 *
 * No JS animation — everything is CSS so it inherits `prefers-reduced-motion`.
 */

import { STAGE_LABELS } from '../types'

type Props = {
  /** The currently-running stage key, e.g. "tts_synthesis". */
  stage?: string | null
  /** Human label from a StagePreviewEvent — more specific than STAGE_LABELS. */
  label?: string
  /** Preview items materialising from the model output. */
  items?: string[]
  /** Running token count for stages with no richer content. */
  tokens?: number
  /** Fan-out progress counter. Only shown when total > 1. */
  count?: { completed: number; total: number }
  /** True when a feedback note is being interpreted before any stage row exists. */
  interpreting?: boolean
}

/** Durations and delays for the eight equalizer bars.
 *  Chosen so they move at different rates and feel organic rather than mechanical. */
const BAR_RHYTHM: ReadonlyArray<{ d: string; delay: string }> = [
  { d: '0.55s', delay: '-80ms' },
  { d: '0.70s', delay: '-220ms' },
  { d: '0.45s', delay: '-10ms' },
  { d: '0.65s', delay: '-300ms' },
  { d: '0.50s', delay: '-150ms' },
  { d: '0.75s', delay: '-55ms' },
  { d: '0.60s', delay: '-185ms' },
  { d: '0.48s', delay: '-120ms' },
]

const COUNT_NOUNS: Record<string, [string, string]> = {
  tts_synthesis: ['line', 'lines'],
  image_generation: ['scene', 'scenes'],
}

export function StreamingActivity({
  stage,
  label,
  items,
  tokens,
  count,
  interpreting,
}: Props) {
  const isTts = stage === 'tts_synthesis'
  const isImage = stage === 'image_generation'
  // Show the most recent five items; the oldest scroll off the visible window.
  const visibleItems = (items ?? []).slice(-5)
  const showFanout = count && count.total > 1
  const pct =
    showFanout ? Math.min(100, Math.round((count!.completed / count!.total) * 100)) : 0
  const [singular, plural] = COUNT_NOUNS[stage ?? ''] ?? ['step', 'steps']

  const displayLabel = interpreting
    ? 'Reading your note'
    : (label ?? (stage ? (STAGE_LABELS[stage] ?? stage.replace(/_/g, ' ')) : ''))

  return (
    <div className="stream-card">
      {/* ── Left column: animated orb ─────────────────────── */}
      <div className="stream-orb-col">
        <svg
          className="stream-orb"
          viewBox="0 0 120 120"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          aria-hidden
        >
          {/* Three concentric rings that expand outward and fade, staggered */}
          <circle className="orb-ring ring-a" cx="60" cy="60" r="22" />
          <circle className="orb-ring ring-b" cx="60" cy="60" r="22" />
          <circle className="orb-ring ring-c" cx="60" cy="60" r="22" />
          {/* Core: solid teal sphere that gently swells */}
          <circle className="orb-core" cx="60" cy="60" r="20" />
          {/* Glass-highlight ellipse — the "shiny dot" on a sphere */}
          <ellipse className="orb-shine" cx="53" cy="52" rx="5.5" ry="3.5" />
          {/* A small dot that orbits the core */}
          <g className="orb-orbit-g">
            <circle className="orb-spark" cx="86" cy="60" r="3.5" />
          </g>
        </svg>

        {/* TTS: audio equalizer bars */}
        {isTts && (
          <div className="wave-bars" aria-hidden>
            {BAR_RHYTHM.map((b, i) => (
              <div
                key={i}
                className="wave-bar"
                style={
                  {
                    '--bar-d': b.d,
                    '--bar-delay': b.delay,
                  } as React.CSSProperties
                }
              />
            ))}
          </div>
        )}

        {/* Image generation: four pulsing placeholder tiles */}
        {isImage && (
          <div className="img-dots" aria-hidden>
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="img-dot"
                style={{ '--dot-delay': `${i * 0.22}s` } as React.CSSProperties}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Right column: content ─────────────────────────── */}
      <div className="stream-body" aria-live="polite">
        <p className="stream-label">{displayLabel}</p>

        {visibleItems.length > 0 && (
          <ul className="stream-items">
            {visibleItems.map((item, i) => (
              <li
                key={`${item}-${i}`}
                className={i === visibleItems.length - 1 ? 'stream-newest' : undefined}
              >
                {item}
              </li>
            ))}
          </ul>
        )}

        {/* Fallback when there are no structured items to preview */}
        {visibleItems.length === 0 && (tokens ?? 0) > 0 && (
          <p className="stream-tokens">
            <span className="stream-cursor" aria-hidden />
            {(tokens ?? 0).toLocaleString()} tokens written
          </p>
        )}

        {/* Fan-out progress bar */}
        {showFanout && (
          <div className="stream-fanout">
            <div className="stream-fanout-track">
              <div
                className="stream-fanout-fill"
                style={{ width: `${pct}%` }}
                role="progressbar"
                aria-valuenow={count!.completed}
                aria-valuemax={count!.total}
                aria-label={`${count!.completed} of ${count!.total} ${count!.total === 1 ? singular : plural}`}
              />
            </div>
            <span className="stream-fanout-note">
              {count!.completed} of {count!.total}{' '}
              {count!.total === 1 ? singular : plural}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
