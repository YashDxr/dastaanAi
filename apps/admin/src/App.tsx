import { apiFetch, ApiError } from '@daastaan/api-types'
import { useEffect, useState } from 'react'
import './App.css'

type CostRow = { label: string; calls: number; cost_usd: number }
type CostSummary = {
  total_usd: number
  budget_cap_usd: number
  remaining_usd: number
  by_stage: CostRow[]
  by_model: CostRow[]
}

/**
 * Starting shell for the operator panel.
 *
 * The spend view comes first because it is the control that actually matters
 * during a budgeted event: it reads the same cost_ledger rows the backend writes
 * on every paid call, so the number here is the real one.
 *
 * This is a separate build from the user app for organisation only. Access is
 * enforced by require_admin on every /api/admin route - a non-admin loading this
 * page gets 403s, not a hidden UI.
 */
export default function App() {
  const [costs, setCosts] = useState<CostSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<CostSummary>('/admin/costs')
      .then(setCosts)
      .catch((err: ApiError) =>
        setError(err.status === 403 ? 'Admin role required.' : err.detail),
      )
  }, [])

  if (error) return <main className="shell"><h1>Daastaan Admin</h1><p className="error">{error}</p></main>
  if (!costs) return <main className="shell">Loading…</main>

  const used = costs.budget_cap_usd > 0 ? (costs.total_usd / costs.budget_cap_usd) * 100 : 0

  return (
    <main className="shell">
      <h1>Daastaan Admin</h1>

      <section>
        <h2>Spend</h2>
        <p className="figure">
          ${costs.total_usd.toFixed(2)} <span>of ${costs.budget_cap_usd.toFixed(2)}</span>
        </p>
        <div className="meter">
          <div className="fill" style={{ width: `${Math.min(used, 100)}%` }} />
        </div>
        <p>${costs.remaining_usd.toFixed(2)} remaining</p>
      </section>

      <section>
        <h2>By stage</h2>
        <table>
          <thead>
            <tr><th>Stage</th><th>Calls</th><th>Cost</th></tr>
          </thead>
          <tbody>
            {costs.by_stage.map((row) => (
              <tr key={row.label}>
                <td>{row.label}</td>
                <td>{row.calls}</td>
                <td>${row.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>By model</h2>
        <table>
          <thead>
            <tr><th>Model</th><th>Calls</th><th>Cost</th></tr>
          </thead>
          <tbody>
            {costs.by_model.map((row) => (
              <tr key={row.label}>
                <td>{row.label}</td>
                <td>{row.calls}</td>
                <td>${row.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  )
}
