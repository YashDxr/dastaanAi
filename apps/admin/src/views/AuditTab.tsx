import { apiFetch } from '@daastaan/api-types'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { AuditEvent } from '../types'

type AuditTabProps = {
  onError: (message: string | null) => void
}

function formatMetadata(metadata: Record<string, unknown> | null | undefined) {
  if (!metadata || Object.keys(metadata).length === 0) return '—'
  return JSON.stringify(metadata)
}

function eventSearchText(event: AuditEvent) {
  return [
    event.action,
    event.actor_user_id,
    event.target_type,
    event.target_id,
    formatMetadata(event.metadata),
  ]
    .filter(Boolean)
    .join(' ')
    .toLocaleLowerCase()
}

function eventIdentity(event: AuditEvent, index: number) {
  return event.id ?? `${event.created_at ?? 'unknown'}-${event.action}-${event.target_id ?? index}`
}

export function AuditTab({ onError }: AuditTabProps) {
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [action, setAction] = useState('all')
  const [targetType, setTargetType] = useState('all')
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    onError(null)
    try {
      const rows = await apiFetch<AuditEvent[]>('/admin/audit?limit=200')
      setEvents(rows)
      setLastUpdated(new Date())
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Unable to load audit history')
    } finally {
      setLoading(false)
    }
  }, [onError])

  useEffect(() => {
    void load()
  }, [load])

  const actions = useMemo(
    () => [...new Set(events.map((event) => event.action))].sort((a, b) => a.localeCompare(b)),
    [events],
  )
  const targetTypes = useMemo(
    () =>
      [...new Set(events.map((event) => event.target_type).filter((type): type is string => Boolean(type)))].sort(
        (a, b) => a.localeCompare(b),
      ),
    [events],
  )
  const filteredEvents = useMemo(() => {
    const queryText = query.trim().toLocaleLowerCase()
    return events.filter((event) => {
      if (action !== 'all' && event.action !== action) return false
      if (targetType !== 'all' && event.target_type !== targetType) return false
      return !queryText || eventSearchText(event).includes(queryText)
    })
  }, [action, events, query, targetType])

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>Audit trail</h2>
          <p className="muted">The latest 200 administrative actions, newest first.</p>
        </div>
        <div className="refresh-group">
          <span className="muted refresh-meta" aria-live="polite">
            {lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Not loaded'}
          </span>
          <button type="button" className="btn ghost" onClick={() => void load()} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="filter-bar" aria-label="Filter audit history">
        <label className="field filter-search">
          <span className="sr-only">Search audit history</span>
          <input
            type="search"
            value={query}
            placeholder="Search action, actor, target, or metadata"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label className="field filter-select">
          <span className="sr-only">Action</span>
          <select value={action} onChange={(event) => setAction(event.target.value)}>
            <option value="all">All actions</option>
            {actions.map((item) => (
              <option value={item} key={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="field filter-select">
          <span className="sr-only">Target type</span>
          <select value={targetType} onChange={(event) => setTargetType(event.target.value)}>
            <option value="all">All targets</option>
            {targetTypes.map((item) => (
              <option value={item} key={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <p className="filter-count" aria-live="polite">
          {filteredEvents.length} of {events.length}
        </p>
      </div>

      {loading && !events.length && <p className="muted">Loading audit history…</p>}

      {!loading && !filteredEvents.length && (
        <p className="muted">
          {events.length ? 'No audit entries match these filters.' : 'No administrative actions recorded yet.'}
        </p>
      )}

      {filteredEvents.length > 0 && (
        <div className="table-scroll">
          <table className="audit-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Target</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {filteredEvents.map((event, index) => (
                <tr key={eventIdentity(event, index)}>
                  <td className="muted audit-time">
                    {event.created_at ? new Date(event.created_at).toLocaleString() : '—'}
                  </td>
                  <td>
                    <code>{event.action}</code>
                  </td>
                  <td className="muted">{event.actor_user_id ?? 'system'}</td>
                  <td>
                    {event.target_type ? <code>{event.target_type}</code> : '—'}
                    {event.target_id && <span className="muted"> · {event.target_id}</span>}
                  </td>
                  <td>
                    {event.metadata && Object.keys(event.metadata).length > 0 ? (
                      <details className="audit-details">
                        <summary>View metadata</summary>
                        <pre>{JSON.stringify(event.metadata, null, 2)}</pre>
                      </details>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
