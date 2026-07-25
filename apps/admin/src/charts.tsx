/** Recharts wrappers carrying the console's palette and axis conventions.
 *
 * Kept in one place so a chart added later cannot drift into a different set of
 * colours or a different money format from the tables next to it.
 */
import type { ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatDuration, formatUsd } from './types'

export const PALETTE = [
  '#1f6f8b',
  '#2f9e8f',
  '#c47f2c',
  '#8b5fa8',
  '#b4573a',
  '#4a7ab8',
  '#5a8f4f',
  '#a8446b',
]

const AXIS = { stroke: '#7b8894', fontSize: 12 }
const GRID = '#e6ebf0'

export function ChartCard({
  title,
  hint,
  children,
  height = 260,
}: {
  title: string
  hint?: string
  children: ReactNode
  height?: number
}) {
  return (
    <section className="chart-card">
      <header>
        <h3>{title}</h3>
        {hint && <p className="muted">{hint}</p>}
      </header>
      <div style={{ width: '100%', height }}>
        <ResponsiveContainer>{children as never}</ResponsiveContainer>
      </div>
    </section>
  )
}

export function EmptyChart({ label }: { label: string }) {
  return <p className="muted chart-empty">{label}</p>
}

type Datum = Record<string, string | number>

/** Horizontal bars, for categorical labels too long to fit on an X axis. */
export function HBar({
  data,
  valueKey,
  kind,
}: {
  data: Datum[]
  valueKey: string
  kind: 'duration' | 'usd' | 'count'
}) {
  const format = (v: number) =>
    kind === 'duration' ? formatDuration(v) : kind === 'usd' ? formatUsd(v) : String(v)
  return (
    <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
      <CartesianGrid stroke={GRID} horizontal={false} />
      <XAxis type="number" tick={AXIS} tickFormatter={format} />
      <YAxis type="category" dataKey="name" tick={AXIS} width={132} />
      <Tooltip formatter={(v) => format(Number(v))} cursor={{ fill: 'rgba(31,111,139,0.06)' }} />
      <Bar dataKey={valueKey} radius={[0, 4, 4, 0]}>
        {data.map((_, i) => (
          <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
        ))}
      </Bar>
    </BarChart>
  )
}

export function StackedTokens({ data }: { data: Datum[] }) {
  return (
    <BarChart data={data} margin={{ left: 4, right: 12, top: 4, bottom: 4 }}>
      <CartesianGrid stroke={GRID} vertical={false} />
      <XAxis dataKey="name" tick={{ ...AXIS, fontSize: 11 }} interval={0} angle={-18} height={56}
             textAnchor="end" />
      <YAxis tick={AXIS} />
      <Tooltip cursor={{ fill: 'rgba(31,111,139,0.06)' }} />
      <Legend wrapperStyle={{ fontSize: 12 }} />
      <Bar dataKey="input" name="Input tokens" stackId="t" fill={PALETTE[0]} />
      <Bar dataKey="output" name="Output tokens" stackId="t" fill={PALETTE[1]} radius={[4, 4, 0, 0]} />
    </BarChart>
  )
}

export function Donut({ data }: { data: Datum[] }) {
  return (
    <PieChart>
      <Pie data={data} dataKey="value" nameKey="name" innerRadius="52%" outerRadius="80%"
           paddingAngle={2}>
        {data.map((_, i) => (
          <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
        ))}
      </Pie>
      <Tooltip formatter={(v) => formatUsd(Number(v))} />
      <Legend wrapperStyle={{ fontSize: 12 }} />
    </PieChart>
  )
}

export function SpendLine({ data }: { data: Datum[] }) {
  return (
    <LineChart data={data} margin={{ left: 4, right: 12, top: 4, bottom: 4 }}>
      <CartesianGrid stroke={GRID} vertical={false} />
      <XAxis dataKey="day" tick={{ ...AXIS, fontSize: 11 }} />
      <YAxis tick={AXIS} tickFormatter={(v) => formatUsd(Number(v))} width={68} />
      <Tooltip formatter={(v) => formatUsd(Number(v))} />
      <Line type="monotone" dataKey="cost_usd" name="Spend" stroke={PALETTE[0]} strokeWidth={2}
            dot={{ r: 3 }} />
    </LineChart>
  )
}
