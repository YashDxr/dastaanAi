import { useId } from 'react'
import {
  PIPELINE_ORDER,
  STAGE_DESCRIPTIONS,
  STAGE_LABELS,
  type Job,
  type Progress,
} from '../types'

type Props = {
  progress: Progress | null
  /** A feedback note is being interpreted; no job row exists for that work yet. */
  interpreting?: boolean
}

function statusFor(jobs: Job[], stage: string): string {
  const matches = jobs.filter((j) => j.stage === stage)
  if (!matches.length) return 'pending'
  return matches[matches.length - 1].status
}

export function ProgressStepper({ progress, interpreting = false }: Props) {
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
  const statuses = stages.map((stage) => {
    const status = statusFor(jobs, stage)
    return ready && status === 'pending' ? 'succeeded' : status
  })
  const done = statuses.filter((s) => s === 'succeeded').length
  const pct = ready ? 100 : Math.round((done / stages.length) * 100)
  const failed = jobs.find((j) => j.status === 'failed')

  const runningIndex = statuses.indexOf('running')
  const runningStage = runningIndex >= 0 ? stages[runningIndex] : null

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
            {STAGE_DESCRIPTIONS[runningStage] ?? 'Working on this stage.'}
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
    </section>
  )
}
