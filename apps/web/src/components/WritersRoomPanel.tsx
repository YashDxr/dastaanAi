import { useCallback, useEffect, useRef, useState } from 'react'
import { stories as storiesApi } from '../api'
import type { WritersRoomSession } from '../types'

const POLL_MS = 3000

type Props = {
  storyId: string
  ready: boolean
}

export function WritersRoomPanel({ storyId, ready }: Props) {
  const [sessions, setSessions] = useState<WritersRoomSession[]>([])
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const cancelled = useRef(false)

  useEffect(() => {
    cancelled.current = false
    return () => { cancelled.current = true }
  }, [])

  const load = useCallback(() => {
    storiesApi.writersRoom(storyId)
      .then((next) => { if (!cancelled.current) setSessions(next) })
      .catch(() => {})
  }, [storyId])

  useEffect(() => { load() }, [load])

  // Poll while any session is pending/running
  useEffect(() => {
    const busy = sessions.some((s) => s.status === 'pending' || s.status === 'running')
    if (!busy) return
    const id = window.setInterval(load, POLL_MS)
    return () => window.clearInterval(id)
  }, [sessions, load])

  async function convene() {
    setWorking(true)
    setError(null)
    try {
      await storiesApi.startWritersRoom(storyId)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start')
    } finally {
      setWorking(false)
    }
  }

  const busy = working || sessions.some((s) => s.status === 'pending' || s.status === 'running')
  const latest = sessions[0] ?? null

  function togglePersona(key: string) {
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  return (
    <div className="analysis-panel writers-room-panel">
      <p className="eyebrow">Writers Room</p>

      <button
        type="button"
        className="btn primary"
        disabled={!ready || busy}
        onClick={convene}
      >
        {busy ? 'Convening...' : 'Convene Writers Room'}
      </button>

      {error && <p className="form-error">{error}</p>}

      {latest?.status === 'failed' && (
        <p className="form-error">{latest.error || 'Analysis failed'}</p>
      )}

      {latest?.result && (
        <div className="analysis-result">
          <div className="wr-brief">
            <div className="wr-score-badge">
              <span className="wr-score-value">{latest.result.brief.overall_score}</span>
              <span className="wr-score-label">/10</span>
            </div>
            <p className="wr-summary">{latest.result.brief.summary}</p>
            {latest.result.brief.key_themes.length > 0 && (
              <div className="wr-themes">
                {latest.result.brief.key_themes.map((t, i) => (
                  <span key={i} className="chip">{t}</span>
                ))}
              </div>
            )}
            {latest.result.brief.priority_actions.length > 0 && (
              <div className="wr-actions">
                <p className="eyebrow">Priority Actions</p>
                <ul>
                  {latest.result.brief.priority_actions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div className="wr-critiques">
            {latest.result.critiques.map((c, i) => {
              const key = `${latest.id}-${i}`
              const isOpen = expanded[key] ?? false
              return (
                <div key={key} className="wr-critique-card">
                  <button
                    type="button"
                    className="wr-critique-header"
                    onClick={() => togglePersona(key)}
                  >
                    <strong>{c.persona}</strong>
                    <span className="wr-critique-caret">{isOpen ? '\u25B2' : '\u25BC'}</span>
                  </button>
                  {isOpen && (
                    <div className="wr-critique-body">
                      {c.strengths.length > 0 && (
                        <div>
                          <p className="wr-section-label wr-strengths-label">Strengths</p>
                          <ul>{c.strengths.map((s, j) => <li key={j}>{s}</li>)}</ul>
                        </div>
                      )}
                      {c.concerns.length > 0 && (
                        <div>
                          <p className="wr-section-label wr-concerns-label">Concerns</p>
                          <ul>{c.concerns.map((s, j) => <li key={j}>{s}</li>)}</ul>
                        </div>
                      )}
                      {c.suggestions.length > 0 && (
                        <div>
                          <p className="wr-section-label">Suggestions</p>
                          <ul>{c.suggestions.map((s, j) => <li key={j}>{s}</li>)}</ul>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
