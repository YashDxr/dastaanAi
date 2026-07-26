import { useCallback, useEffect, useRef, useState } from 'react'
import { stories as storiesApi } from '../api'
import type { StoryGenomeAnalysis } from '../types'

const POLL_MS = 3000

type Props = {
  storyId: string
  ready: boolean
}

export function StoryGenomePanel({ storyId, ready }: Props) {
  const [analyses, setAnalyses] = useState<StoryGenomeAnalysis[]>([])
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const cancelled = useRef(false)

  useEffect(() => {
    cancelled.current = false
    return () => { cancelled.current = true }
  }, [])

  const load = useCallback(() => {
    storiesApi.genome(storyId)
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
      await storiesApi.startGenome(storyId)
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

  // Sort character balance descending for the stacked bar
  const charEntries = result
    ? Object.entries(result.character_balance).sort(([, a], [, b]) => b - a)
    : []

  // Colour palette for the stacked bar segments
  const BAR_COLORS = [
    'var(--teal)', 'var(--amber)', '#6b5bd6', 'var(--ok)',
    'var(--danger)', '#e67e73', '#5bc0de', '#8e7cc3',
  ]

  return (
    <div className="analysis-panel genome-panel">
      <p className="eyebrow">Story DNA</p>

      <button
        type="button"
        className="btn primary"
        disabled={!ready || busy}
        onClick={analyze}
      >
        {busy ? 'Analyzing...' : 'Analyze DNA'}
      </button>

      {error && <p className="form-error">{error}</p>}

      {latest?.status === 'failed' && (
        <p className="form-error">{latest.error || 'Analysis failed'}</p>
      )}

      {result && (
        <div className="analysis-result">
          <p className="genome-summary">{result.summary}</p>

          <div className="genome-badges">
            <span className="chip">{result.arc_shape}</span>
            <span className="chip">{result.pacing_profile}</span>
            <span className="chip">Dialogue {Math.round(result.dialogue_ratio * 100)}%</span>
          </div>

          {result.traits.length > 0 && (
            <div className="genome-traits">
              <p className="eyebrow">Story Traits</p>
              {result.traits.map((t, i) => (
                <div key={i} className="genome-trait">
                  <div className="genome-trait-header">
                    <span className="genome-trait-name">{t.trait}</span>
                    <span className="genome-trait-value">{Math.round(t.value * 100)}%</span>
                  </div>
                  <div className="genome-trait-bar">
                    <div
                      className="genome-trait-fill"
                      style={{ width: `${t.value * 100}%` }}
                    />
                  </div>
                  <p className="genome-trait-explain">{t.explanation}</p>
                </div>
              ))}
            </div>
          )}

          {charEntries.length > 0 && (
            <div className="genome-characters">
              <p className="eyebrow">Character Balance</p>
              <div className="genome-char-bar">
                {charEntries.map(([name, pct], i) => (
                  <div
                    key={name}
                    className="genome-char-segment"
                    style={{
                      width: `${pct * 100}%`,
                      background: BAR_COLORS[i % BAR_COLORS.length],
                    }}
                    title={`${name}: ${Math.round(pct * 100)}%`}
                  />
                ))}
              </div>
              <div className="genome-char-legend">
                {charEntries.map(([name, pct], i) => (
                  <span key={name} className="genome-char-label">
                    <span
                      className="genome-char-dot"
                      style={{ background: BAR_COLORS[i % BAR_COLORS.length] }}
                    />
                    {name} ({Math.round(pct * 100)}%)
                  </span>
                ))}
              </div>
            </div>
          )}

          {result.concepts.length > 0 && (
            <div className="genome-concepts">
              <p className="eyebrow">Inspired By This</p>
              <div className="genome-concept-cards">
                {result.concepts.map((c, i) => (
                  <div key={i} className="genome-concept-card">
                    <strong>{c.title}</strong>
                    <p>{c.premise}</p>
                    <p className="genome-concept-why">{c.why_similar}</p>
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
