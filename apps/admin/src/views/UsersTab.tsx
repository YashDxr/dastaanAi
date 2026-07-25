import { apiFetch } from '@daastaan/api-types'
import { useCallback, useEffect, useState } from 'react'
import { ChartCard, Donut, EmptyChart, HBar, SpendLine } from '../charts'
import type { UserCostDetail, UserSummary } from '../types'
import { formatUsd, prettyStage } from '../types'

function UserDashboard({ detail, onBack }: { detail: UserCostDetail; onBack: () => void }) {
  const { user } = detail
  const stageData = detail.by_stage
    .filter((r) => r.cost_usd > 0)
    .map((r) => ({ name: prettyStage(r.label), cost: r.cost_usd }))
  const modelData = detail.by_model
    .filter((r) => r.cost_usd > 0)
    .map((r) => ({ name: r.label, value: r.cost_usd }))
  const storyData = detail.by_story
    .filter((r) => r.cost_usd > 0)
    .map((r) => ({ name: r.title ?? r.story_id.slice(0, 8), cost: r.cost_usd }))

  return (
    <>
      <section className="panel run-header">
        <button type="button" className="btn ghost" onClick={onBack}>
          ← All users
        </button>
        <div>
          <p className="eyebrow">{user.role}</p>
          <h2>{user.email}</h2>
          <p className="muted">
            Joined {new Date(user.created_at).toLocaleDateString()}
            {user.last_active_at
              ? ` · last spend ${new Date(user.last_active_at).toLocaleString()}`
              : ' · no spend recorded'}
          </p>
        </div>
      </section>

      <section className="stat-grid">
        <Stat label="Total spend" value={formatUsd(user.cost_usd)} />
        <Stat label="Stories" value={String(user.stories)} />
        <Stat label="API calls" value={String(user.calls)} />
        <Stat
          label="Tokens"
          value={(user.input_tokens + user.output_tokens).toLocaleString()}
          hint={`${user.input_tokens.toLocaleString()} in / ${user.output_tokens.toLocaleString()} out`}
        />
        <Stat label="Cache hits" value={String(user.cache_hits)} />
        <Stat
          label="Cache savings"
          value={formatUsd(detail.cache_savings_usd)}
          hint="estimated"
        />
      </section>

      <div className="chart-grid">
        <ChartCard title="Spend over time" hint="Daily total across every story.">
          {detail.daily.length ? (
            <SpendLine data={detail.daily as never} />
          ) : (
            <EmptyChart label="No spend recorded for this account." />
          )}
        </ChartCard>

        <ChartCard title="Cost by model">
          {modelData.length ? <Donut data={modelData} /> : <EmptyChart label="No spend yet." />}
        </ChartCard>

        <ChartCard title="Cost by stage">
          {stageData.length ? (
            <HBar data={stageData} valueKey="cost" kind="usd" />
          ) : (
            <EmptyChart label="No spend yet." />
          )}
        </ChartCard>

        <ChartCard title="Cost by story">
          {storyData.length ? (
            <HBar data={storyData} valueKey="cost" kind="usd" />
          ) : (
            <EmptyChart label="No spend yet." />
          )}
        </ChartCard>
      </div>

      <section className="panel">
        <h3>Per-story spend</h3>
        {detail.by_story.length ? (
          <table>
            <thead>
              <tr>
                <th>Story</th>
                <th>Calls</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {detail.by_story.map((row) => (
                <tr key={row.story_id}>
                  <td>{row.title ?? row.story_id}</td>
                  <td>{row.calls}</td>
                  <td>{formatUsd(row.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">This account has not generated anything yet.</p>
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

export function UsersTab({ onError }: { onError: (message: string | null) => void }) {
  const [users, setUsers] = useState<UserSummary[]>([])
  const [detail, setDetail] = useState<UserCostDetail | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    apiFetch<UserSummary[]>('/admin/users')
      .then(setUsers)
      .catch((err: Error) => onError(err.message))
      .finally(() => setLoading(false))
  }, [onError])

  useEffect(load, [load])

  if (detail) {
    return <UserDashboard detail={detail} onBack={() => setDetail(null)} />
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>Accounts</h2>
          <p className="muted">Select an account to see where its credits went.</p>
        </div>
      </div>

      {loading && <p className="muted">Loading accounts…</p>}

      {!loading && (
        <table className="clickable">
          <thead>
            <tr>
              <th>Email</th>
              <th>Stories</th>
              <th>Calls</th>
              <th>Spend</th>
              <th>Last active</th>
              <th>Role</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr
                key={u.id}
                tabIndex={0}
                onClick={() => {
                  onError(null)
                  void apiFetch<UserCostDetail>(`/admin/users/${u.id}/costs`)
                    .then(setDetail)
                    .catch((err: Error) => onError(err.message))
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') e.currentTarget.click()
                }}
              >
                <td>{u.email}</td>
                <td>{u.stories}</td>
                <td>{u.calls}</td>
                <td>{formatUsd(u.cost_usd)}</td>
                <td className="muted">
                  {u.last_active_at ? new Date(u.last_active_at).toLocaleDateString() : '—'}
                </td>
                <td>
                  {/* Stops the row's drill-down from firing when changing a role. */}
                  <select
                    className="role-select"
                    value={u.role}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => {
                      const role = e.target.value
                      void apiFetch(`/admin/users/${u.id}/role`, {
                        method: 'PUT',
                        body: JSON.stringify({ role }),
                      })
                        .then(load)
                        .catch((err: Error) => onError(err.message))
                    }}
                  >
                    <option value="user">user</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
