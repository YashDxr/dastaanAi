export type User = { id: string; email: string; role: string }

export type CostRow = {
  label: string
  calls: number
  cost_usd: number
  input_tokens?: number
  output_tokens?: number
}

export type CostSummary = {
  total_usd: number
  budget_cap_usd: number
  remaining_usd: number
  cache_savings_usd: number
  cache_hits: number
  by_stage: CostRow[]
  by_model: CostRow[]
}

export type RunSummary = {
  version_id: string
  story_id: string
  story_title: string | null
  user_id: string | null
  user_email: string | null
  version_number: number
  genre: string | null
  mood: string | null
  is_regen: boolean
  regen_scope: string | null
  regen_stage: string | null
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  stage_count: number
  calls: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
  cache_hits: number
  error: string | null
}

export type RunStage = {
  stage: string
  status: string
  attempt: number
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  error: string | null
  cost_usd: number
  input_tokens: number
  output_tokens: number
  calls: number
  cache_hits: number
}

export type ModelCost = {
  label: string
  calls: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
  cache_hits: number
}

export type RunDetail = {
  version_id: string
  story_id: string
  story_title: string | null
  user_email: string | null
  version_number: number
  genre: string | null
  mood: string | null
  parent_version_id: string | null
  is_regen: boolean
  directive: {
    scope?: string
    target_stage?: string
    target_id?: string | null
    instruction_delta?: string
  } | null
  feedback_text: string | null
  status: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  cost_usd: number
  input_tokens: number
  output_tokens: number
  calls: number
  cache_hits: number
  stages: RunStage[]
  by_model: ModelCost[]
  assets: { kind: string; count: number; duration_ms: number }[]
}

export type UserSummary = {
  id: string
  email: string
  role: string
  created_at: string
  stories: number
  calls: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
  cache_hits: number
  last_active_at: string | null
}

export type UserCostDetail = {
  user: UserSummary
  daily: { day: string; cost_usd: number; calls: number }[]
  by_stage: CostRow[]
  by_model: CostRow[]
  by_story: { story_id: string; title: string | null; cost_usd: number; calls: number }[]
  cache_savings_usd: number
}

export type AdminSetting = {
  key: string
  value: Record<string, unknown>
  updated_by: string | null
  updated_at: string
}

/** Stage identifiers are snake_case in the database; charts want them readable. */
export function prettyStage(stage: string) {
  return stage.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

export function formatDuration(ms: number | null | undefined) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const minutes = Math.floor(ms / 60_000)
  return `${minutes}m ${Math.round((ms % 60_000) / 1000)}s`
}

export function formatUsd(value: number) {
  if (value === 0) return '$0.00'
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`
}
