import { ApiError, apiFetch } from '@daastaan/api-types'
import { useCallback, useEffect, useState } from 'react'
import './App.css'

type User = { id: string; email: string; role: string }
type CostRow = { label: string; calls: number; cost_usd: number; input_tokens?: number; output_tokens?: number }
type CostSummary = {
  total_usd: number
  budget_cap_usd: number
  remaining_usd: number
  by_stage: CostRow[]
  by_model: CostRow[]
}
type PipelineRun = {
  id: string
  version_id: string
  status: string
  error: string | null
  langfuse_trace_id: string | null
  mlflow_run_id: string | null
  started_at: string
}
type AdminSetting = {
  key: string
  value: Record<string, unknown>
  updated_by: string | null
  updated_at: string
}

type Tab = 'spend' | 'users' | 'runs' | 'settings'

function formatError(err: unknown) {
  if (err instanceof ApiError) return typeof err.detail === 'string' ? err.detail : 'Request failed'
  if (err instanceof Error) return err.message
  return 'Something went wrong'
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [tab, setTab] = useState<Tab>('spend')
  const [costs, setCosts] = useState<CostSummary | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [runs, setRuns] = useState<PipelineRun[]>([])
  const [settings, setSettings] = useState<AdminSetting[]>([])
  const [error, setError] = useState<string | null>(null)
  const [boot, setBoot] = useState(true)
  const [authPending, setAuthPending] = useState(false)

  const loadTab = useCallback(async (next: Tab) => {
    setError(null)
    try {
      if (next === 'spend') setCosts(await apiFetch<CostSummary>('/admin/costs'))
      if (next === 'users') setUsers(await apiFetch<User[]>('/admin/users'))
      if (next === 'runs') setRuns(await apiFetch<PipelineRun[]>('/admin/runs?failed_only=false'))
      if (next === 'settings') setSettings(await apiFetch<AdminSetting[]>('/admin/settings'))
    } catch (err) {
      setError(formatError(err))
    }
  }, [])

  useEffect(() => {
    apiFetch<User>('/auth/me')
      .then(async (me) => {
        setUser(me)
        if (me.role === 'admin') await loadTab('spend')
        else setError('Admin role required. Log in with an admin account.')
      })
      .catch(() => setUser(null))
      .finally(() => setBoot(false))
  }, [loadTab])

  async function login(form: HTMLFormElement) {
    const data = new FormData(form)
    setAuthPending(true)
    setError(null)
    try {
      const me = await apiFetch<User>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          email: data.get('email'),
          password: data.get('password'),
        }),
      })
      setUser(me)
      if (me.role !== 'admin') {
        setError('Admin role required.')
        return
      }
      await loadTab('spend')
      setTab('spend')
    } catch (err) {
      setError(formatError(err))
    } finally {
      setAuthPending(false)
    }
  }

  if (boot) {
    return (
      <main className="admin-shell">
        <p className="muted">Loading console…</p>
      </main>
    )
  }

  if (!user || user.role !== 'admin') {
    return (
      <main className="auth-card">
        <p className="eyebrow">Daastaan</p>
        <h1>Operator console</h1>
        <p className="muted">Sign in with an admin account to manage spend, users, and runs.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void login(e.currentTarget)
          }}
        >
          <label>
            Email
            <input name="email" type="email" required autoComplete="username" />
          </label>
          <label>
            Password
            <input name="password" type="password" required autoComplete="current-password" />
          </label>
          {error && <p className="error">{error}</p>}
          <button className="btn primary" type="submit" disabled={authPending}>
            {authPending ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </main>
    )
  }

  const used = costs && costs.budget_cap_usd > 0 ? (costs.total_usd / costs.budget_cap_usd) * 100 : 0

  return (
    <main className="admin-shell">
      <header className="admin-top">
        <div>
          <p className="eyebrow">Daastaan admin</p>
          <h1>Operations</h1>
          <p className="muted">{user.email}</p>
        </div>
        <button
          type="button"
          className="btn ghost"
          onClick={async () => {
            await apiFetch('/auth/logout', { method: 'POST' })
            setUser(null)
          }}
        >
          Log out
        </button>
      </header>

      <nav className="admin-tabs" aria-label="Admin sections">
        {(
          [
            ['spend', 'Spend'],
            ['users', 'Users'],
            ['runs', 'Runs'],
            ['settings', 'Settings'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={tab === id ? 'active' : ''}
            onClick={() => {
              setTab(id)
              void loadTab(id)
            }}
          >
            {label}
          </button>
        ))}
      </nav>

      {error && <p className="error">{error}</p>}

      {tab === 'spend' && costs && (
        <>
          <section className="panel">
            <h2>Budget</h2>
            <p className="figure">
              ${costs.total_usd.toFixed(2)}
              <span>of ${costs.budget_cap_usd.toFixed(2)}</span>
            </p>
            <div className="meter">
              <div className="fill" style={{ width: `${Math.min(used, 100)}%` }} />
            </div>
            <p className="muted">${costs.remaining_usd.toFixed(2)} remaining</p>
          </section>
          <section className="panel">
            <h2>By stage</h2>
            <CostTable rows={costs.by_stage} />
          </section>
          <section className="panel">
            <h2>By model</h2>
            <CostTable rows={costs.by_model} />
          </section>
        </>
      )}

      {tab === 'users' && (
        <section className="panel">
          <h2>Accounts</h2>
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.email}</td>
                  <td>{u.role}</td>
                  <td>
                    <select
                      className="role-select"
                      value={u.role}
                      onChange={(e) => {
                        const role = e.target.value
                        void apiFetch(`/admin/users/${u.id}/role`, {
                          method: 'PUT',
                          body: JSON.stringify({ role }),
                        })
                          .then(() => loadTab('users'))
                          .catch((err) => setError(formatError(err)))
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
        </section>
      )}

      {tab === 'runs' && (
        <section className="panel">
          <h2>Recent pipeline runs</h2>
          <ul className="stack-list">
            {runs.map((run) => (
              <li key={run.id}>
                <strong>{run.version_id}</strong>
                <span className={`badge ${run.status}`}>{run.status}</span>
                <p className="muted">
                  {new Date(run.started_at).toLocaleString()}
                  {run.error ? ` · ${run.error}` : ''}
                </p>
              </li>
            ))}
            {!runs.length && <li className="muted">No runs yet.</li>}
          </ul>
        </section>
      )}

      {tab === 'settings' && (
        <section className="panel">
          <h2>Runtime settings</h2>
          <ul className="stack-list">
            {settings.map((s) => (
              <li key={s.key}>
                <strong>{s.key}</strong>
                <pre className="muted" style={{ whiteSpace: 'pre-wrap', margin: '0.4rem 0 0' }}>
                  {JSON.stringify(s.value, null, 2)}
                </pre>
              </li>
            ))}
            {!settings.length && (
              <li className="muted">
                No overrides yet. Workers fall back to `.env` model defaults until you add keys like
                `models` or `budget_cap_usd`.
              </li>
            )}
          </ul>
        </section>
      )}
    </main>
  )
}

function CostTable({ rows }: { rows: CostRow[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Label</th>
          <th>Calls</th>
          <th>Cost</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label}>
            <td>{row.label}</td>
            <td>{row.calls}</td>
            <td>${row.cost_usd.toFixed(4)}</td>
          </tr>
        ))}
        {!rows.length && (
          <tr>
            <td colSpan={3} className="muted">
              No spend recorded yet.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  )
}
