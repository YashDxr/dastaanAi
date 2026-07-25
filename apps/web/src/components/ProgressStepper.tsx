import { STAGE_LABELS, PIPELINE_ORDER, type Job, type Progress } from '../types'

type Props = {
  progress: Progress | null
}

function statusFor(jobs: Job[], stage: string): string {
  const matches = jobs.filter((j) => j.stage === stage)
  if (!matches.length) return 'pending'
  return matches[matches.length - 1].status
}

export function ProgressStepper({ progress }: Props) {
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
        {stages.map((stage, i) => (
          <li key={stage} className={`stage-chip ${statuses[i]}`}>
            <span className="stage-dot" aria-hidden />
            <span className="stage-label">{STAGE_LABELS[stage] ?? stage}</span>
          </li>
        ))}
      </ol>
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
