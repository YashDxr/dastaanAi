import { apiFetch } from '@daastaan/api-types'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChartCard, Donut, EmptyChart, HBar, PALETTE, StackedTokens } from '../charts'
import type { RunDetail, RunStage, RunSummary } from '../types'
import { formatDuration, formatUsd, prettyStage } from '../types'

function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${status}`}>{status}</span>
}

/** Stage start/finish laid out against the run's own wall clock.
 *
 * Fan-out stages overlap heavily - TTS and image generation run at the same
 * time - which a bar chart of durations hides and this makes obvious.
 */
function Timeline({ stages, startedAt, durationMs }: {
  stages: RunStage[]
  startedAt: string | null
  durationMs: number | null
}) {
  const timed = stages.filter((s) => s.started_at && s.duration_ms != null)
  if (!startedAt || !durationMs || !timed.length) {
    return <EmptyChart label="No stage timings recorded for this run." />
  }
  const origin = new Date(startedAt).getTime()
  return (
    <ul className="timeline">
      {timed.map((stage, i) => {
        const offset = ((new Date(stage.started_at!).getTime() - origin) / durationMs) * 100
        const width = ((stage.duration_ms ?? 0) / durationMs) * 100
        return (
          <li key={`${stage.stage}-${i}`}>
            <span className="timeline-label">{prettyStage(stage.stage)}</span>
            <span className="timeline-track">
              <span
                className={`timeline-bar ${stage.status}`}
                style={{
                  marginLeft: `${Math.max(0, Math.min(offset, 99))}%`,
                  width: `${Math.max(width, 0.8)}%`,
                  background: stage.status === 'failed' ? undefined : PALETTE[i % PALETTE.length],
                }}
              />
            </span>
            <span className="timeline-value">{formatDuration(stage.duration_ms)}</span>
          </li>
        )
      })}
    </ul>
  )
}

function RunDashboard({
  run,
  onBack,
  onRefresh,
  autoRefresh,
  setAutoRefresh,
  refreshing,
  lastUpdated,
}: {
  run: RunDetail
  onBack: () => void
  onRefresh: () => void
  autoRefresh: boolean
  setAutoRefresh: (next: boolean) => void
  refreshing: boolean
  lastUpdated: Date | null
}) {
  const durationData = run.stages
    .filter((s) => s.duration_ms != null)
    .map((s) => ({ name: prettyStage(s.stage), duration: s.duration_ms ?? 0 }))
  const tokenData = run.stages
    .filter((s) => s.input_tokens + s.output_tokens > 0)
    .map((s) => ({ name: prettyStage(s.stage), input: s.input_tokens, output: s.output_tokens }))
  const costData = run.stages
    .filter((s) => s.cost_usd > 0)
    .map((s) => ({ name: prettyStage(s.stage), cost: s.cost_usd }))
  const modelData = run.by_model
    .filter((m) => m.cost_usd > 0)
    .map((m) => ({ name: m.label, value: m.cost_usd }))
  const failures = run.stages.filter((s) => s.status === 'failed')

  return (
    <>
      <section className="panel run-header">
        <button type="button" className="btn ghost" onClick={onBack}>
          ← All runs
        </button>
        <div>
          <p className="eyebrow">
            {run.is_regen ? 'Regeneration' : 'First generation'} · v{run.version_number}
          </p>
          <h2>{run.story_title ?? 'Untitled story'}</h2>
          <p className="muted">
            {run.user_email ?? 'unknown user'} · {new Date(run.created_at).toLocaleString()}
            {run.genre ? ` · ${run.genre}` : ''}
          </p>
        </div>
        <div className="run-dashboard-actions">
          <StatusBadge status={run.status} />
          <label className="toggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
            />
            Auto-refresh
          </label>
          <button type="button" className="btn ghost small" onClick={onRefresh} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
          <p className="live-status" aria-live="polite">
            <span className={`live-dot ${autoRefresh ? 'is-live' : ''}`} aria-hidden="true" />
            {autoRefresh ? 'Live updates' : 'Updates paused'}
            {lastUpdated ? ` · ${lastUpdated.toLocaleTimeString()}` : ''}
          </p>
        </div>
      </section>

      <section className="stat-grid">
        <Stat label="Duration" value={formatDuration(run.duration_ms)} />
        <Stat label="Cost" value={formatUsd(run.cost_usd)} />
        <Stat label="API calls" value={String(run.calls)} />
        <Stat
          label="Tokens"
          value={(run.input_tokens + run.output_tokens).toLocaleString()}
          hint={`${run.input_tokens.toLocaleString()} in / ${run.output_tokens.toLocaleString()} out`}
        />
        <Stat label="Stages" value={String(run.stages.length)} />
        <Stat label="Cache hits" value={String(run.cache_hits)} />
      </section>

      {(run.directive || run.feedback_text) && (
        <section className="panel">
          <h3>What this run was asked to change</h3>
          {run.feedback_text && <p className="quote">"{run.feedback_text}"</p>}
          {run.directive && (
            <p className="muted">
              Scope <strong>{run.directive.scope}</strong> · entry stage{' '}
              <strong>{prettyStage(run.directive.target_stage ?? '')}</strong>
              {run.directive.target_id ? ` · target ${run.directive.target_id}` : ''}
              {run.directive.instruction_delta
                ? ` · note: "${run.directive.instruction_delta}"`
                : ''}
            </p>
          )}
        </section>
      )}

      {failures.length > 0 && (
        <section className="panel failure-panel">
          <h3>Failed stages</h3>
          <ul className="stack-list">
            {failures.map((s) => (
              <li key={s.stage}>
                <strong>{prettyStage(s.stage)}</strong>
                <span className="muted"> · attempt {s.attempt}</span>
                <pre className="error-text">{s.error ?? 'No error text recorded.'}</pre>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="chart-grid">
        <ChartCard title="Time per stage" hint="Where the wall clock actually went.">
          {durationData.length ? (
            <HBar data={durationData} valueKey="duration" kind="duration" />
          ) : (
            <EmptyChart label="No stage timings recorded." />
          )}
        </ChartCard>

        <ChartCard title="Cost per stage">
          {costData.length ? (
            <HBar data={costData} valueKey="cost" kind="usd" />
          ) : (
            <EmptyChart label="No spend recorded for this run." />
          )}
        </ChartCard>

        <ChartCard title="Tokens per stage" hint="Audio and image stages spend without tokens.">
          {tokenData.length ? (
            <StackedTokens data={tokenData} />
          ) : (
            <EmptyChart label="No token usage in this run." />
          )}
        </ChartCard>

        <ChartCard title="Cost by model">
          {modelData.length ? <Donut data={modelData} /> : <EmptyChart label="No spend recorded." />}
        </ChartCard>
      </div>

      <section className="panel">
        <h3>Stage timeline</h3>
        <Timeline stages={run.stages} startedAt={run.started_at} durationMs={run.duration_ms} />
      </section>

      <section className="panel">
        <h3>Stage detail</h3>
        <table>
          <thead>
            <tr>
              <th>Stage</th>
              <th>Status</th>
              <th>Duration</th>
              <th>Calls</th>
              <th>Tokens</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {run.stages.map((s, i) => (
              <tr key={`${s.stage}-${i}`}>
                <td>{prettyStage(s.stage)}</td>
                <td>
                  <StatusBadge status={s.status} />
                </td>
                <td>{formatDuration(s.duration_ms)}</td>
                <td>{s.calls}</td>
                <td>{(s.input_tokens + s.output_tokens).toLocaleString()}</td>
                <td>{formatUsd(s.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h3>Assets produced</h3>
        {run.assets.length ? (
          <table>
            <thead>
              <tr>
                <th>Kind</th>
                <th>Count</th>
                <th>Audio length</th>
              </tr>
            </thead>
            <tbody>
              {run.assets.map((a) => (
                <tr key={a.kind}>
                  <td>{prettyStage(a.kind)}</td>
                  <td>{a.count}</td>
                  <td>{a.duration_ms ? formatDuration(a.duration_ms) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No assets recorded against this version.</p>
        )}
      </section>
    </>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat">
      <p className="stat-label">{label}</p>
      <p className="stat-value">{value}</p>
      {hint && <p className="muted stat-hint">{hint}</p>}
    </div>
  )
}

export function RunsTab({ onError }: { onError: (message: string | null) => void }) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [selected, setSelected] = useState<RunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [openingVersionId, setOpeningVersionId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [kindFilter, setKindFilter] = useState<'all' | 'full' | 'regeneration'>('all')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const loadRuns = useCallback(
    async (initial = false) => {
      if (initial) setLoading(true)
      setRefreshing(true)
      onError(null)
      try {
        const failedOnly = statusFilter === 'failed'
        const next = await apiFetch<RunSummary[]>(`/admin/runs?failed_only=${failedOnly}`)
        setRuns(next)
        setLastUpdated(new Date())
      } catch (err) {
        onError(err instanceof Error ? err.message : 'Unable to load pipeline runs')
      } finally {
        setLoading(false)
        setRefreshing(false)
      }
    },
    [onError, statusFilter],
  )

  const loadRunDetail = useCallback(
    async (versionId: string) => {
      setOpeningVersionId(versionId)
      setRefreshing(true)
      onError(null)
      try {
        const detail = await apiFetch<RunDetail>(`/admin/runs/${versionId}`)
        setSelected(detail)
        setLastUpdated(new Date())
      } catch (err) {
        onError(err instanceof Error ? err.message : 'Unable to load run detail')
      } finally {
        setOpeningVersionId(null)
        setRefreshing(false)
      }
    },
    [onError],
  )

  useEffect(() => {
    void loadRuns(true)
  }, [loadRuns])

  useEffect(() => {
    if (!autoRefresh || selected) return undefined
    const interval = window.setInterval(() => void loadRuns(), 10_000)
    return () => window.clearInterval(interval)
  }, [autoRefresh, loadRuns, selected])

  useEffect(() => {
    if (!autoRefresh || !selected || !['pending', 'running'].includes(selected.status)) return undefined
    const interval = window.setInterval(() => void loadRunDetail(selected.version_id), 10_000)
    return () => window.clearInterval(interval)
  }, [autoRefresh, loadRunDetail, selected])

  const statuses = useMemo(
    () => [...new Set(runs.map((run) => run.status))].sort((a, b) => a.localeCompare(b)),
    [runs],
  )
  const filteredRuns = useMemo(() => {
    const queryText = query.trim().toLocaleLowerCase()
    return runs.filter((run) => {
      if (statusFilter !== 'all' && run.status !== statusFilter) return false
      if (kindFilter === 'full' && run.is_regen) return false
      if (kindFilter === 'regeneration' && !run.is_regen) return false
      if (!queryText) return true
      return [run.story_title, run.user_email, run.genre, run.mood, run.version_id]
        .filter(Boolean)
        .join(' ')
        .toLocaleLowerCase()
        .includes(queryText)
    })
  }, [kindFilter, query, runs, statusFilter])

  if (selected) {
    return (
      <RunDashboard
        run={selected}
        onBack={() => setSelected(null)}
        onRefresh={() => void loadRunDetail(selected.version_id)}
        autoRefresh={autoRefresh}
        setAutoRefresh={setAutoRefresh}
        refreshing={refreshing}
        lastUpdated={lastUpdated}
      />
    )
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>Pipeline runs</h2>
          <p className="muted">
            One row per generated version. Select a run to open its dashboard.
          </p>
        </div>
        <div className="refresh-group">
          <label className="toggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
            />
            Auto-refresh
          </label>
          <button type="button" className="btn ghost" onClick={() => void loadRuns()} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="live-summary" aria-live="polite">
        <span className={`live-dot ${autoRefresh ? 'is-live' : ''}`} aria-hidden="true" />
        {autoRefresh ? 'Live list updates every 10 seconds.' : 'Auto-refresh is paused.'}
        {lastUpdated ? ` Last updated ${lastUpdated.toLocaleTimeString()}.` : ''}
      </div>

      <div className="filter-bar" aria-label="Filter pipeline runs">
        <label className="field filter-search">
          <span className="sr-only">Search pipeline runs</span>
          <input
            type="search"
            value={query}
            placeholder="Search story, user, genre, or version"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className="field filter-select">
          <span className="sr-only">Run status</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="all">All statuses</option>
            {statuses.map((status) => (
              <option value={status} key={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
        <label className="field filter-select">
          <span className="sr-only">Generation kind</span>
          <select
            value={kindFilter}
            onChange={(event) => setKindFilter(event.target.value as 'all' | 'full' | 'regeneration')}
          >
            <option value="all">All kinds</option>
            <option value="full">First generation</option>
            <option value="regeneration">Regeneration</option>
          </select>
        </label>
        <p className="filter-count" aria-live="polite">
          {filteredRuns.length} of {runs.length}
        </p>
      </div>

      {loading && <p className="muted">Loading runs…</p>}

      {!loading && !filteredRuns.length && (
        <p className="muted">
          {runs.length ? 'No runs match these filters. ' : 'No runs yet. '}
          Runs appear as soon as a story is generated.
        </p>
      )}

      {!loading && filteredRuns.length > 0 && (
        <table className="clickable">
          <thead>
            <tr>
              <th>Story</th>
              <th>User</th>
              <th>Kind</th>
              <th>Status</th>
              <th>Duration</th>
              <th>Cost</th>
              <th>When</th>
            </tr>
          </thead>
          <tbody>
            {filteredRuns.map((run) => (
              <tr
                key={run.version_id}
                tabIndex={0}
                role="button"
                aria-label={`Open run ${run.story_title ?? 'Untitled'} version ${run.version_number}`}
                onClick={() => {
                  void loadRunDetail(run.version_id)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    e.currentTarget.click()
                  }
                }}
              >
                <td>
                  {run.story_title ?? 'Untitled'} <span className="muted">v{run.version_number}</span>
                  {openingVersionId === run.version_id && <span className="muted"> · opening…</span>}
                </td>
                <td className="muted">{run.user_email ?? '—'}</td>
                <td>{run.is_regen ? `Regen · ${run.regen_scope ?? 'scoped'}` : 'Full'}</td>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td>{formatDuration(run.duration_ms)}</td>
                <td>{formatUsd(run.cost_usd)}</td>
                <td className="muted">{new Date(run.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
