import { useId } from 'react'
import { emptyLive, percentComplete, type LiveProgress } from '../live'
import {
  PIPELINE_ORDER,
  STAGE_DESCRIPTIONS,
  STAGE_LABELS,
  type Job,
  type Progress,
} from '../types'

type Props = {
  progress: Progress | null
  /** The live overlay. Absent only before the first event of a session arrives. */
  live?: LiveProgress
  /** A feedback note is being interpreted; no job row exists for that work yet. */
  interpreting?: boolean
}

/** What the fan-out stages are counting, so "12 of 40" says what it is counting. */
const COUNT_NOUNS: Record<string, [string, string]> = {
  tts_synthesis: ['line recorded', 'lines recorded'],
  image_generation: ['scene painted', 'scenes painted'],
}

function statusFor(jobs: Job[], stage: string): string {
  const matches = jobs.filter((j) => j.stage === stage)
  if (!matches.length) return 'pending'
  return matches[matches.length - 1].status
}

function countLabel(stage: string, completed: number, total: number): string {
  const [singular, plural] = COUNT_NOUNS[stage] ?? ['step done', 'steps done']
  return `${completed} of ${total} ${total === 1 ? singular : plural}`
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

  // Counts come from the stream while it is up and from `/jobs` when it is not, so
  // the fan-out stays quantified either way. Folded with `max` for the same reason
  // the reducer does it: concurrent workers report out of order.
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
      <div
        className="progress-meter"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <ol className="stage-rail">
        {stages.map((stage, i) => {
          const description = STAGE_DESCRIPTIONS[stage]
          const count = counts[stage]
          return (
            <li key={stage} className={`stage-chip ${statuses[i]}`}>
              {/* A button, not a div with a title: hover explains it to a mouse
                  user and focus explains it to everyone else. */}
              <button
                type="button"
                className="stage-trigger"
                aria-describedby={description ? `${tipId}-${stage}` : undefined}
              >
                <span className="stage-dot" aria-hidden />
                <span className="stage-label">{STAGE_LABELS[stage] ?? stage}</span>
                {/* Only while the stage is in flight: once it has finished the
                    fraction is always n of n, which is just noise on the rail. */}
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

      <div className="stage-explainer" aria-live="polite">
        {interpreting ? (
          <p>
            <strong>Reading your note.</strong> Working out the smallest change that
            satisfies it before any audio is re-recorded.
          </p>
        ) : runningStage ? (
          <p>
            <strong>{STAGE_LABELS[runningStage] ?? runningStage}.</strong>{' '}
            {runningCount && runningCount.total > 0
              ? countLabel(runningStage, runningCount.completed, runningCount.total)
              : (STAGE_DESCRIPTIONS[runningStage] ?? 'Working on this stage.')}
          </p>
        ) : ready ? (
          <p className="muted">
            Every stage finished. Hover any step above to see what it did.
          </p>
        ) : (
          <p className="muted">
            {partial
              ? 'Queued. Only the steps above will re-run.'
              : 'Queued. Hover any step above to see what it will do.'}
          </p>
        )}
      </div>

      {/* What the model is writing, as it writes it. Newest last, matching the
          order it was generated, and capped so a long script does not push the
          rest of the studio off screen. */}
      {preview && preview.items.length > 0 && (
        <section className="stage-preview" aria-label={preview.label}>
          <p className="eyebrow">{preview.label}</p>
          <ul>
            {preview.items.slice(-6).map((item, i) => (
              // Indexed key: these are model-written strings with no id, and two
              // lines of dialogue can legitimately be identical.
              <li key={`${item}-${i}`}>{item}</li>
            ))}
          </ul>
        </section>
      )}
      {/* A stage with no preview worth showing still needs to look alive, and this
          is the only honest signal available mid-call. */}
      {!preview && tokens !== undefined && tokens > 0 && (
        <p className="progress-note" aria-live="polite">
          Writing… {tokens} tokens so far.
        </p>
      )}

      {partial && (
        <p className="progress-note">
          Everything else is carried over from the previous take.
        </p>
      )}
      {failed && (
        <p className="progress-error">
          {failed.error ?? 'A stage failed. Check your API key and try again.'}
        </p>
      )}
      {unavailableMusic && <p className="progress-note">{unavailableMusic.error}</p>}
      {/* Degradations the stream reported that no job row carries yet. */}
      {!unavailableMusic &&
        live.notices.map((notice) => (
          <p className="progress-note" key={notice}>
            {notice}
          </p>
        ))}
    </section>
  )
}
