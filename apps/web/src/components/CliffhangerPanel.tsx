import { useCallback, useEffect, useRef, useState } from 'react'
import { stories as storiesApi } from '../api'
import type { CliffhangerAnalysis } from '../types'

const POLL_MS = 3000

type Props = {
  storyId: string
  ready: boolean
}

export function CliffhangerPanel({ storyId, ready }: Props) {
  const [analyses, setAnalyses] = useState<CliffhangerAnalysis[]>([])
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const cancelled = useRef(false)

  useEffect(() => {
    cancelled.current = false
    return () => { cancelled.current = true }
  }, [])

  const load = useCallback(() => {
    storiesApi.cliffhanger(storyId)
      .then((next) => { if (!cancelled.current) setAnalyses(next) })
      .catch(() => {})
  }, [storyId])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const busy = analyses.some((a) => a.status === 'pending' || a.status === 'running')
    if (!busy) return
    const id = window.setInterval(load, POLL_MS)
    return () => window.clearInterval(id)
  }, [analyses, load])

  async function analyze() {
    setWorking(true)
    setError(null)
    try {
      await storiesApi.startCliffhanger(storyId)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start')
    } finally {
      setWorking(false)
    }
  }

  const busy = working || analyses.some((a) => a.status === 'pending' || a.status === 'running')
  const latest = analyses[0] ?? null
  const result = latest?.result

  return (
    <div className="analysis-panel cliffhanger-panel">
      <p className="eyebrow">Cliffhanger Optimizer</p>

      <button
        type="button"
        className="btn primary"
        disabled={!ready || busy}
        onClick={analyze}
      >
        {busy ? 'Analyzing...' : 'Analyze Ending'}
      </button>

      {error && <p className="form-error">{error}</p>}

      {latest?.status === 'failed' && (
        <p className="form-error">{latest.error || 'Analysis failed'}</p>
      )}

      {result && (
        <div className="analysis-result">
          <div className="ch-scores">
            <div className="ch-score-item">
              <div className="ch-score-bar">
                <div
                  className="ch-score-fill"
                  style={{ width: `${result.current_score * 10}%` }}
                />
              </div>
              <span className="ch-score-num">{result.current_score}/10</span>
              <span className="ch-score-label">Current Score</span>
            </div>
            <div className="ch-score-item">
              <div className="ch-score-bar">
                <div
                  className="ch-score-fill ch-tension"
                  style={{ width: `${result.tension * 10}%` }}
                />
              </div>
              <span className="ch-score-num">{result.tension}/10</span>
              <span className="ch-score-label">Tension</span>
            </div>
          </div>

          <p className="ch-analysis">{result.current_analysis}</p>

          {result.unresolved_threads.length > 0 && (
            <div className="ch-threads">
              <p className="eyebrow">Unresolved Threads</p>
              <ul>
                {result.unresolved_threads.map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
              </ul>
            </div>
          )}

          {result.suggestions.length > 0 && (
            <div className="ch-suggestions">
              <p className="eyebrow">Alternative Endings</p>
              <div className="ch-suggestion-cards">
                {result.suggestions.map((s, i) => (
                  <div key={i} className="ch-suggestion-card">
                    <div className="ch-suggestion-header">
                      <strong>{s.title}</strong>
                      <span className="ch-binge-badge">{s.binge_probability}% binge</span>
                    </div>
                    <p className="ch-suggestion-sketch">{s.sketch}</p>
                    <div className="ch-suggestion-meta">
                      <span>Tension: {s.tension_score}/10</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
