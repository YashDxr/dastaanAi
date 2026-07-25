import { useId } from 'react'
import { emptyLive, percentComplete, type LiveProgress } from '../live'
import {
  PIPELINE_ORDER,
  STAGE_DESCRIPTIONS,
  STAGE_LABELS,
  type Job,
  type Progress,
} from '../types'
import { StreamingActivity } from './StreamingActivity'

type Props = {
  progress: Progress | null
  /** The live overlay. Absent only before the first event of a session arrives. */
  live?: LiveProgress
  /** A feedback note is being interpreted; no job row exists for that work yet. */
  interpreting?: boolean
}

function statusFor(jobs: Job[], stage: string): string {
  const matches = jobs.filter((j) => j.stage === stage)
  if (!matches.length) return 'pending'
  return matches[matches.length - 1].status
}

export function ProgressStepper({ progress, live = emptyLive, interpreting = false }: Props) {
  const tipId = useId()
  const jobs = progress?.jobs ?? []
  // A regeneration re-runs a slice of the pipeline, not all of it. Measuring it
  // against every stage would read as though the story had restarted from zero.
  const stages = progress?.planned_stages?.length ? progress.planned_stages : PIPELINE_ORDER
  const partial = stages.length < PIPELINE_ORDER.length

  // Assembly is the last thing to run, so a ready story has by definition cleared
  // every planned stage — including runs recorded before the fan-out stages
  // reported themselves, which would otherwise sit at 80% forever.
  const ready = progress?.status === 'ready'

  // The live overlay wins over the polled row: both describe the same transition,
  // and the event carrying it arrived without waiting for a round trip.
  const resolve = (stage: string): string => {
    const status = live.stages[stage] ?? statusFor(jobs, stage)
    return ready && status === 'pending' ? 'succeeded' : status
  }

  // Counts come from the stream while it is up and from `/jobs` when it is not,
  // so the fan-out stays quantified either way. Folded with `max` for the same
  // reason the reducer does it: concurrent workers report out of order.
  const counts: LiveProgress['counts'] = { ...live.counts }
  for (const entry of progress?.stage_progress ?? []) {
    const current = counts[entry.stage]
    counts[entry.stage] = {
      completed: Math.max(current?.completed ?? 0, entry.completed),
      total: Math.max(current?.total ?? 0, entry.total),
    }
  }

  const statuses = stages.map(resolve)
  const pct = percentComplete(stages, resolve, counts, ready)
  const failed = jobs.find((j) => j.status === 'failed')
  const unavailableMusic = jobs.find(
    (j) => j.stage === 'music_generation' && j.status === 'skipped' && j.error,
  )

  const runningIndex = statuses.indexOf('running')
  const runningStage = runningIndex >= 0 ? stages[runningIndex] : null
  const runningCount = runningStage ? counts[runningStage] : undefined
  const preview = runningStage ? live.previews[runningStage] : undefined
  const tokens = runningStage ? live.tokens[runningStage] : undefined

  // Show the streaming card while a stage is active or while a feedback note is
  // being interpreted (before the stage row appears).
  const streaming = runningStage !== null || interpreting

  return (
    <section className="progress-panel" aria-label="Generation progress">
      <div className="progress-top">
        <div>
          <p className="eyebrow">{partial ? 'Revising' : 'Studio progress'}</p>
          <h2>
            {pct === 100
              ? 'Episode ready'
              : partial
                ? 'Reworking your notes'
                : 'Crafting your drama'}
          </h2>
        </div>
        <p className="progress-pct" aria-live="polite">
          {pct}%
        </p>
      </div>

      {/* Smooth progress bar */}
      <div
        className="progress-meter"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>

      {/* Stage chips rail */}
      <ol className="stage-rail">
        {stages.map((stage, i) => {
          const description = STAGE_DESCRIPTIONS[stage]
          const count = counts[stage]
          return (
            <li key={stage} className={`stage-chip ${statuses[i]}`}>
              {/* A button rather than a div: hover explains it to a mouse user
                  and focus explains it to keyboard and AT users. */}
              <button
                type="button"
                className="stage-trigger"
                aria-describedby={description ? `${tipId}-${stage}` : undefined}
              >
                <span className="stage-dot" aria-hidden />
                <span className="stage-label">{STAGE_LABELS[stage] ?? stage}</span>
                {/* Count badge only while in flight. Finished means n/n which is noise. */}
                {statuses[i] === 'running' && count && count.total > 1 && (
                  <span className="stage-count">
                    {count.completed}/{count.total}
                  </span>
                )}
              </button>
              {description && (
                <span role="tooltip" id={`${tipId}-${stage}`} className="stage-tip">
                  <strong>{STAGE_LABELS[stage] ?? stage}</strong>
                  {description}
                </span>
              )}
            </li>
          )
        })}
      </ol>

      {/* ── Live streaming card ──────────────────────────────────────────── */}
      {streaming ? (
        <StreamingActivity
          stage={runningStage}
          label={preview?.label}
          items={preview?.items}
          tokens={tokens}
          count={runningCount}
          interpreting={interpreting && !runningStage}
        />
      ) : ready ? (
        <p className="progress-note muted">
          Every stage finished. Hover any step above to see what it did.
        </p>
      ) : (
        <p className="progress-note muted">
          {partial
            ? 'Queued. Only the steps above will re-run.'
            : 'Queued. Hover any step above to see what it will do.'}
        </p>
      )}

      {partial && (
        <p className="progress-note">Everything else is carried over from the previous take.</p>
      )}
      {failed && (
        <p className="progress-error">
          {failed.error ?? 'A stage failed. Check your API key and try again.'}
        </p>
      )}
      {unavailableMusic && <p className="progress-note">{unavailableMusic.error}</p>}
      {/* Degradations reported through the stream that no job row carries yet. */}
      {!unavailableMusic &&
        live.notices.map((notice) => (
          <p className="progress-note" key={notice}>
            {notice}
          </p>
        ))}
    </section>
  )
}
