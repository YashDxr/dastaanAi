import { useCallback, useEffect, useState } from 'react'
import { formatError, stories as storiesApi } from '../api'
import type { ConsistencyCheck, ConsistencyFinding, StoryState } from '../types'

type Props = {
  storyId: string
  state: StoryState
  disabled?: boolean
}

const POLL_MS = 1800

function findingLocation(finding: ConsistencyFinding, state: StoryState): string {
  const scene = state.scenes.find((item) => item.id === finding.scene_id)
  const line = finding.line_id ? state.lines.find((item) => item.id === finding.line_id) : null
  const sceneLabel = scene ? `Scene ${scene.index + 1}: ${scene.title}` : 'Referenced scene'
  if (!line) return sceneLabel
  const excerpt = line.text.length > 90 ? `${line.text.slice(0, 90).trimEnd()}…` : line.text
  return `${sceneLabel} · ${line.speaker}: “${excerpt}”`
}

function statusCopy(check: ConsistencyCheck): string {
  if (check.status === 'pending') return 'Queued for a continuity review…'
  if (check.status === 'running') return 'Checking character knowledge, timeline and cause-and-effect…'
  if (check.status === 'failed') return check.error ?? 'The check could not be completed.'
  return check.summary ?? 'Continuity review complete.'
}

/** A standalone polling surface because this task must not alter pipeline progress. */
export function ConsistencyPanel({ storyId, state, disabled }: Props) {
  const [checks, setChecks] = useState<ConsistencyCheck[]>([])
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const next = await storiesApi.consistencyChecks(storyId)
      setChecks(next)
      setError(null)
    } catch (err) {
      setError(formatError(err))
    } finally {
      setLoading(false)
    }
  }, [storyId])

  useEffect(() => {
    setChecks([])
    setLoading(true)
    void refresh()
  }, [refresh])

  const latest = checks[0]
  const active = latest?.status === 'pending' || latest?.status === 'running'

  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void refresh(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [active, refresh])

  async function startCheck() {
    setStarting(true)
    setError(null)
    try {
      const started = await storiesApi.startConsistencyCheck(storyId)
      setChecks((current) => [started, ...current.filter((item) => item.id !== started.id)])
      // The worker may have started between the POST response and this paint.
      // Re-read once so the status always comes from the durable API record.
      await refresh()
    } catch (err) {
      setError(formatError(err))
    } finally {
      setStarting(false)
    }
  }

  const blocked = Boolean(disabled || starting || active)
  const hasFindings = latest?.status === 'succeeded' && latest.findings.length > 0

  return (
    <section className="consistency-panel" aria-live="polite">
      <div className="section-head consistency-head">
        <div>
          <p className="eyebrow">Quality pass</p>
          <h2>Plot Hole Hunter</h2>
          <p className="muted">
            Looks across scenes and spoken lines for continuity conflicts before you share it.
          </p>
        </div>
        <button type="button" className="btn ghost" disabled={blocked} onClick={() => void startCheck()}>
          {starting || active ? 'Checking…' : latest?.status === 'succeeded' ? 'Check again' : 'Check consistency'}
        </button>
      </div>

      {loading && !latest && <p className="muted consistency-status">Loading review history…</p>}
      {!loading && !latest && !error && (
        <p className="muted consistency-status">No review yet. This never changes your story.</p>
      )}
      {latest && <p className={`consistency-status ${latest.status}`}>{statusCopy(latest)}</p>}
      {error && <p className="form-error">{error}</p>}

      {hasFindings && (
        <ul className="consistency-findings">
          {latest.findings.map((finding, index) => (
            <li
              key={`${finding.scene_id}:${finding.line_id ?? 'scene'}:${index}`}
              className={finding.severity}
            >
              <div className="finding-meta">
                <span className={`finding-severity ${finding.severity}`}>{finding.severity}</span>
                <span>{finding.type.replaceAll('_', ' ')}</span>
              </div>
              <p className="finding-location">{findingLocation(finding, state)}</p>
              <p>{finding.explanation}</p>
              <p className="finding-suggestion">
                <strong>Suggested fix:</strong> {finding.suggestion}
              </p>
            </li>
          ))}
        </ul>
      )}

      {latest?.status === 'succeeded' && !hasFindings && (
        <p className="consistency-clear">No material contradictions were found in this version.</p>
      )}
    </section>
  )
}
