import { apiFetch } from '@daastaan/api-types'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { AdminSetting } from '../types'

type SettingsTabProps = {
  onError: (message: string | null) => void
}

type ModelOverride = {
  id: string
  stage: string
  model: string
}

type FeatureFlag = {
  id: string
  name: string
  enabled: boolean
}

const MODEL_KINDS = [
  {
    key: 'reasoning',
    label: 'Reasoning',
    hint: 'Story understanding, continuity, and other deep planning calls.',
  },
  {
    key: 'light',
    label: 'Lightweight',
    hint: 'Shorter structured or editorial calls.',
  },
  {
    key: 'tts',
    label: 'Speech',
    hint: 'Narration and dialogue generation.',
  },
  {
    key: 'image',
    label: 'Image',
    hint: 'Scene artwork generation.',
  },
] as const

const MODEL_KIND_SET = new Set<string>(MODEL_KINDS.map((kind) => kind.key))
const SUGGESTED_KEYS = ['budget_cap_usd', 'models', 'feature_flags', 'voice_presets']

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function asStringMap(value: Record<string, unknown> | undefined) {
  return Object.fromEntries(
    Object.entries(value ?? {}).filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
  )
}

function asBooleanMap(value: Record<string, unknown> | undefined) {
  return Object.fromEntries(
    Object.entries(value ?? {}).filter(
      (entry): entry is [string, boolean] => typeof entry[1] === 'boolean',
    ),
  )
}

function nonStringValues(value: Record<string, unknown> | undefined) {
  return Object.fromEntries(Object.entries(value ?? {}).filter(([, entry]) => typeof entry !== 'string'))
}

function nonBooleanValues(value: Record<string, unknown> | undefined) {
  return Object.fromEntries(Object.entries(value ?? {}).filter(([, entry]) => typeof entry !== 'boolean'))
}

function makeRowId(prefix: string, index: number) {
  return `${prefix}-${index}-${Math.random().toString(36).slice(2, 8)}`
}

function formatUpdated(setting: AdminSetting | undefined) {
  if (!setting?.updated_at) return 'Not saved as an override yet.'
  return `Last saved ${new Date(setting.updated_at).toLocaleString()}${
    setting.updated_by ? ` by ${setting.updated_by}` : ''
  }.`
}

function SettingMeta({ setting }: { setting: AdminSetting | undefined }) {
  return <p className="field-hint setting-meta">{formatUpdated(setting)}</p>
}

function SaveButton({ saving, children = 'Save changes' }: { saving: boolean; children?: string }) {
  return (
    <button type="submit" className="btn primary" disabled={saving}>
      {saving ? 'Saving…' : children}
    </button>
  )
}

/**
 * Runtime settings have a deliberately loose JSON contract on the server. The
 * common keys below are constrained in the UI, while the advanced editor gives
 * operators full access to supported settings such as voice presets.
 */
export function SettingsTab({ onError }: SettingsTabProps) {
  const [settings, setSettings] = useState<AdminSetting[]>([])
  const [loading, setLoading] = useState(true)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [savedKey, setSavedKey] = useState<string | null>(null)
  const [budget, setBudget] = useState('')
  const [budgetTouched, setBudgetTouched] = useState(false)
  const [models, setModels] = useState<Record<string, string>>({})
  const [stageOverrides, setStageOverrides] = useState<ModelOverride[]>([])
  const [flags, setFlags] = useState<FeatureFlag[]>([])
  const [newFlagName, setNewFlagName] = useState('')
  const [advancedKey, setAdvancedKey] = useState('')
  const [advancedJson, setAdvancedJson] = useState('{}')
  const [advancedError, setAdvancedError] = useState<string | null>(null)

  const settingsByKey = useMemo(
    () => Object.fromEntries(settings.map((setting) => [setting.key, setting])),
    [settings],
  )
  const budgetSetting = settingsByKey.budget_cap_usd
  const modelsSetting = settingsByKey.models
  const featureFlagsSetting = settingsByKey.feature_flags

  const hydrateDrafts = useCallback((rows: AdminSetting[]) => {
    const next = Object.fromEntries(rows.map((setting) => [setting.key, setting]))
    const budgetValue = next.budget_cap_usd?.value.amount
    setBudget(typeof budgetValue === 'number' && Number.isFinite(budgetValue) ? String(budgetValue) : '')
    setBudgetTouched(false)

    const modelValues = asStringMap(next.models?.value)
    setModels(
      Object.fromEntries(
        MODEL_KINDS.map((kind) => [kind.key, modelValues[kind.key] ?? '']),
      ),
    )
    setStageOverrides(
      Object.entries(modelValues)
        .filter(([key]) => !MODEL_KIND_SET.has(key))
        .map(([stage, model], index) => ({ id: makeRowId('model', index), stage, model })),
    )

    setFlags(
      Object.entries(asBooleanMap(next.feature_flags?.value)).map(([name, enabled], index) => ({
        id: makeRowId('flag', index),
        name,
        enabled,
      })),
    )
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    onError(null)
    try {
      const rows = await apiFetch<AdminSetting[]>('/admin/settings')
      setSettings(rows)
      hydrateDrafts(rows)
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Unable to load runtime settings')
    } finally {
      setLoading(false)
    }
  }, [hydrateDrafts, onError])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    const keys = [...new Set([...SUGGESTED_KEYS, ...settings.map((setting) => setting.key)])]
    setAdvancedKey((current) => current || keys[0])
  }, [settings])

  useEffect(() => {
    if (!advancedKey) return
    setAdvancedJson(JSON.stringify(settingsByKey[advancedKey]?.value ?? {}, null, 2))
    setAdvancedError(null)
  }, [advancedKey, settingsByKey])

  const saveSetting = useCallback(
    async (key: string, value: Record<string, unknown>) => {
      setSavingKey(key)
      setSavedKey(null)
      onError(null)
      try {
        const saved = await apiFetch<AdminSetting>(`/admin/settings/${encodeURIComponent(key)}`, {
          method: 'PUT',
          body: JSON.stringify({ value }),
        })
        setSettings((current) => {
          const withoutSaved = current.filter((setting) => setting.key !== saved.key)
          return [...withoutSaved, saved].sort((a, b) => a.key.localeCompare(b.key))
        })
        setSavedKey(key)
      } catch (err) {
        onError(err instanceof Error ? err.message : `Unable to save ${key}`)
      } finally {
        setSavingKey(null)
      }
    },
    [onError],
  )

  const modelExtras = nonStringValues(modelsSetting?.value)
  const featureFlagExtras = nonBooleanValues(featureFlagsSetting?.value)
  const parsedBudget = Number(budget)
  const budgetIsValid = budget.trim() !== '' && Number.isFinite(parsedBudget) && parsedBudget >= 0
  const duplicateFlagNames = flags.some(
    (flag, index) =>
      Boolean(flag.name.trim()) &&
      flags.some((other, otherIndex) => otherIndex !== index && other.name.trim() === flag.name.trim()),
  )
  const hasBlankFlagName = flags.some((flag) => !flag.name.trim())

  const changeStageOverride = (id: string, field: 'stage' | 'model', value: string) => {
    setStageOverrides((current) =>
      current.map((override) => (override.id === id ? { ...override, [field]: value } : override)),
    )
  }

  const changeFlag = (id: string, field: 'name' | 'enabled', value: string | boolean) => {
    setFlags((current) =>
      current.map((flag) => (flag.id === id ? { ...flag, [field]: value } as FeatureFlag : flag)),
    )
  }

  const addFlag = () => {
    const name = newFlagName.trim()
    if (!name) return
    if (flags.some((flag) => flag.name === name)) {
      onError(`Feature flag “${name}” already exists.`)
      return
    }
    setFlags((current) => [...current, { id: makeRowId('flag', current.length), name, enabled: false }])
    setNewFlagName('')
    onError(null)
  }

  const knownAndExistingKeys = [...new Set([...SUGGESTED_KEYS, ...settings.map((setting) => setting.key)])]

  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Runtime controls</h2>
            <p className="muted">
              Saved controls apply to the next worker task. Existing jobs keep the settings they
              started with.
            </p>
          </div>
          <button type="button" className="btn ghost" onClick={() => void load()} disabled={loading}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
        {savedKey && <p className="success" role="status">Saved {savedKey}.</p>}
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Budget cap</h2>
            <p className="muted">Generation stops once total ledger spend reaches this amount.</p>
          </div>
        </div>
        <form
          className="setting-form setting-form-compact"
          onSubmit={(event) => {
            event.preventDefault()
            setBudgetTouched(true)
            if (budgetIsValid) void saveSetting('budget_cap_usd', { amount: parsedBudget })
          }}
        >
          <label className="field">
            <span>Maximum spend (USD)</span>
            <input
              inputMode="decimal"
              min="0"
              step="0.01"
              type="number"
              value={budget}
              onChange={(event) => {
                setBudgetTouched(true)
                setBudget(event.target.value)
              }}
              aria-describedby="budget-help"
            />
          </label>
          <div className="form-actions">
            <SaveButton saving={savingKey === 'budget_cap_usd'}>Save cap</SaveButton>
            {budgetTouched && !budgetIsValid && (
              <span className="error-text">Enter a zero or positive amount.</span>
            )}
          </div>
        </form>
        <p id="budget-help" className="field-hint">
          This writes <code>{'{ "amount": number }'}</code> to <code>budget_cap_usd</code>.
        </p>
        <SettingMeta setting={budgetSetting} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Model routing</h2>
            <p className="muted">Override a model family globally or route a specific pipeline stage.</p>
          </div>
        </div>
        <form
          className="setting-form"
          onSubmit={(event) => {
            event.preventDefault()
            const stageValues = Object.fromEntries(
              stageOverrides
                .filter((override) => override.stage.trim() && override.model.trim())
                .map((override) => [override.stage.trim(), override.model.trim()]),
            )
            const globalValues = Object.fromEntries(
              MODEL_KINDS.filter((kind) => models[kind.key]?.trim()).map((kind) => [
                kind.key,
                models[kind.key].trim(),
              ]),
            )
            void saveSetting('models', { ...modelExtras, ...globalValues, ...stageValues })
          }}
        >
          <div className="setting-grid">
            {MODEL_KINDS.map((kind) => (
              <label className="field" key={kind.key}>
                <span>{kind.label}</span>
                <input
                  type="text"
                  value={models[kind.key] ?? ''}
                  placeholder="Use environment default"
                  onChange={(event) =>
                    setModels((current) => ({ ...current, [kind.key]: event.target.value }))
                  }
                />
                <small className="field-hint">{kind.hint}</small>
              </label>
            ))}
          </div>

          <div className="subsection-head">
            <div>
              <h3>Stage overrides</h3>
              <p className="field-hint">A stage override takes priority over its model family.</p>
            </div>
            <button
              type="button"
              className="btn ghost small"
              onClick={() =>
                setStageOverrides((current) => [
                  ...current,
                  { id: makeRowId('model', current.length), stage: '', model: '' },
                ])
              }
            >
              Add override
            </button>
          </div>
          <div className="override-list">
            {stageOverrides.map((override) => (
              <div className="override-row" key={override.id}>
                <label className="field">
                  <span className="sr-only">Pipeline stage</span>
                  <input
                    type="text"
                    value={override.stage}
                    placeholder="stage, e.g. dialogue"
                    onChange={(event) => changeStageOverride(override.id, 'stage', event.target.value)}
                  />
                </label>
                <label className="field">
                  <span className="sr-only">Model identifier</span>
                  <input
                    type="text"
                    value={override.model}
                    placeholder="model identifier"
                    onChange={(event) => changeStageOverride(override.id, 'model', event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="btn ghost small danger-action"
                  aria-label={`Remove ${override.stage || 'new'} stage override`}
                  onClick={() =>
                    setStageOverrides((current) => current.filter((entry) => entry.id !== override.id))
                  }
                >
                  Remove
                </button>
              </div>
            ))}
            {!stageOverrides.length && <p className="muted empty-inline">No per-stage overrides.</p>}
          </div>
          <div className="form-actions">
            <SaveButton saving={savingKey === 'models'}>Save model routing</SaveButton>
          </div>
        </form>
        <SettingMeta setting={modelsSetting} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Feature flags</h2>
            <p className="muted">Enable or pause named runtime switches without changing deployment config.</p>
          </div>
        </div>
        <form
          className="setting-form"
          onSubmit={(event) => {
            event.preventDefault()
            if (hasBlankFlagName || duplicateFlagNames) return
            const values = Object.fromEntries(
              flags.map((flag) => [flag.name.trim(), flag.enabled]),
            )
            void saveSetting('feature_flags', { ...featureFlagExtras, ...values })
          }}
        >
          <div className="flag-list">
            {flags.map((flag) => (
              <div className="flag-row" key={flag.id}>
                <label className="field flag-name-field">
                  <span className="sr-only">Feature flag name</span>
                  <input
                    type="text"
                    value={flag.name}
                    placeholder="flag name"
                    onChange={(event) => changeFlag(flag.id, 'name', event.target.value)}
                  />
                </label>
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={flag.enabled}
                    onChange={(event) => changeFlag(flag.id, 'enabled', event.target.checked)}
                  />
                  <span>{flag.enabled ? 'Enabled' : 'Disabled'}</span>
                </label>
                <button
                  type="button"
                  className="btn ghost small danger-action"
                  aria-label={`Remove ${flag.name || 'new'} feature flag`}
                  onClick={() => setFlags((current) => current.filter((entry) => entry.id !== flag.id))}
                >
                  Remove
                </button>
              </div>
            ))}
            {!flags.length && <p className="muted empty-inline">No boolean feature flags saved.</p>}
          </div>
          <div className="add-row">
            <label className="field">
              <span className="sr-only">New feature flag name</span>
              <input
                type="text"
                value={newFlagName}
                placeholder="new_flag"
                onChange={(event) => setNewFlagName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault()
                    addFlag()
                  }
                }}
              />
            </label>
            <button type="button" className="btn ghost small" onClick={addFlag}>
              Add flag
            </button>
          </div>
          {(hasBlankFlagName || duplicateFlagNames) && (
            <p className="error-text">Feature flag names must be unique and cannot be blank.</p>
          )}
          <div className="form-actions">
            <SaveButton saving={savingKey === 'feature_flags'}>Save feature flags</SaveButton>
          </div>
        </form>
        <SettingMeta setting={featureFlagsSetting} />
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>Advanced JSON</h2>
            <p className="muted">Use this for voice presets or a full JSON view of the supported controls above.</p>
          </div>
        </div>
        <form
          className="setting-form"
          onSubmit={(event) => {
            event.preventDefault()
            const key = advancedKey.trim()
            if (!key) {
              setAdvancedError('Enter a setting key.')
              return
            }
            try {
              const parsed: unknown = JSON.parse(advancedJson)
              if (!isRecord(parsed)) {
                setAdvancedError('Settings must be a JSON object.')
                return
              }
              setAdvancedError(null)
              void saveSetting(key, parsed)
            } catch {
              setAdvancedError('Enter valid JSON before saving.')
            }
          }}
        >
          <label className="field advanced-key-field">
            <span>Setting key</span>
            <input
              list="setting-keys"
              type="text"
              value={advancedKey}
              placeholder="voice_presets"
              onChange={(event) => setAdvancedKey(event.target.value)}
            />
            <datalist id="setting-keys">
              {knownAndExistingKeys.map((key) => (
                <option value={key} key={key} />
              ))}
            </datalist>
          </label>
          <label className="field">
            <span>JSON object</span>
            <textarea
              className="json-editor"
              value={advancedJson}
              spellCheck={false}
              onChange={(event) => setAdvancedJson(event.target.value)}
              aria-describedby="advanced-help"
            />
          </label>
          <p id="advanced-help" className="field-hint">
            Saving a known key here replaces that setting. Use the typed controls above for routine edits.
          </p>
          {advancedError && <p className="error-text">{advancedError}</p>}
          <div className="form-actions">
            <SaveButton saving={savingKey === advancedKey.trim()}>Save JSON</SaveButton>
          </div>
        </form>
        <SettingMeta setting={settingsByKey[advancedKey]} />
      </section>
    </>
  )
}
