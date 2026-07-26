import { useState, useMemo } from 'react'
import { stories as storiesApi, formatError } from '../api'
import type { StoryState } from '../types'

type Scope = 'line' | 'character' | 'scene' | 'music' | 'full_story'

type Props = {
  storyId: string
  state: StoryState | null
  busy: boolean
  regenerating: boolean
  onRegenerated: () => void
}

/** Map a scope to the pipeline stage the regeneration enters at. */
const SCOPE_STAGE: Record<Scope, string> = {
  line: 'tts_synthesis',
  character: 'voice_assignment',
  scene: 'emotion_tagging',
  music: 'music_generation',
  full_story: 'story_understanding',
}

/** Human-readable cost preview per scope. */
function costPreview(
  scope: Scope,
  targetId: string | null,
  state: StoryState | null,
): string {
  if (scope === 'line') {
    const line = state?.lines.find((l) => l.id === targetId)
    if (line) return `This will re-record 1 line (${line.speaker}).`
    return 'This will re-record 1 line.'
  }
  if (scope === 'character') {
    const char = state?.characters.find((c) => c.id === targetId)
    const count = state?.lines.filter((l) => l.character_id === targetId).length ?? 0
    if (char) return `This will re-cast and re-record all of ${char.name}'s lines (${count} line${count !== 1 ? 's' : ''}).`
    return 'This will re-cast and re-record all lines for this character.'
  }
  if (scope === 'scene') {
    const scene = state?.scenes.find((s) => s.id === targetId)
    const count = state?.lines.filter((l) => l.scene_id === targetId).length ?? 0
    if (scene) return `This will re-tag emotions and re-record ${count} line${count !== 1 ? 's' : ''} in "${scene.title}".`
    return 'This will re-process this scene.'
  }
  if (scope === 'music') return 'This will regenerate the background score.'
  return 'This will rewrite and re-record the whole episode.'
}

function truncate(text: string, max = 40) {
  return text.length > max ? `${text.slice(0, max).trimEnd()}...` : text
}

/** Build a natural-language instruction from the slider/chip state. */
function buildInstruction(
  scope: Scope,
  sliders: { pace: number; warmth: number; urgency: number },
  moodChips: string[],
  toneChips: string[],
): string {
  if (scope === 'music') {
    if (moodChips.length === 0) return 'Regenerate the background score.'
    return `Make the background score ${moodChips.join(', ')}.`
  }

  if (scope === 'full_story') {
    if (toneChips.length === 0) return 'Regenerate the whole episode.'
    return `Adjust the overall tone to be ${toneChips.join(', ')}.`
  }

  // line / character / scene
  const parts: string[] = []
  if (sliders.urgency > 0) parts.push('more urgency')
  else if (sliders.urgency < 0) parts.push('a calmer delivery')
  if (sliders.warmth > 0) parts.push('more warmth')
  else if (sliders.warmth < 0) parts.push('a cooler tone')
  if (sliders.pace < 0) parts.push('a slightly slower pace')
  else if (sliders.pace > 0) parts.push('a slightly faster pace')

  if (parts.length === 0) {
    const target = scope === 'line' ? 'this line' : scope === 'character' ? "this character's lines" : 'this scene'
    return `Re-deliver ${target} with clearer emotional intent.`
  }

  const prefix = scope === 'line'
    ? 'Deliver this line with'
    : scope === 'character'
      ? "Deliver this character's lines with"
      : 'Deliver this scene with'

  return `${prefix} ${parts.join(' and ')}.`
}

const SLIDER_LABELS = {
  pace: ['Slower', 'Faster'],
  warmth: ['Cooler', 'Warmer'],
  urgency: ['Calmer', 'More urgent'],
} as const

const MOOD_OPTIONS = ['darker', 'brighter', 'more suspenseful', 'gentler', 'more energetic']
const TONE_OPTIONS = ['more dramatic', 'lighter', 'more intimate', 'grittier', 'more whimsical']

export function DirectorControls({ storyId, state, busy, regenerating, onRegenerated }: Props) {
  const [scope, setScope] = useState<Scope>('line')
  const [targetId, setTargetId] = useState<string | null>(null)
  const [pace, setPace] = useState(0)
  const [warmth, setWarmth] = useState(0)
  const [urgency, setUrgency] = useState(0)
  const [moodChips, setMoodChips] = useState<string[]>([])
  const [toneChips, setToneChips] = useState<string[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const locked = busy || regenerating || pending

  const lines = useMemo(() => state?.lines ?? [], [state?.lines])
  const characters = useMemo(() => state?.characters ?? [], [state?.characters])
  const scenes = useMemo(() => state?.scenes ?? [], [state?.scenes])

  const needsPicker = scope === 'line' || scope === 'character' || scope === 'scene'
  const showSliders = scope === 'line' || scope === 'character' || scope === 'scene'
  const showMoodChips = scope === 'music'
  const showToneChips = scope === 'full_story'

  // Reset target when scope changes
  function handleScopeChange(newScope: Scope) {
    setScope(newScope)
    setTargetId(null)
    setPace(0)
    setWarmth(0)
    setUrgency(0)
    setMoodChips([])
    setToneChips([])
    setError(null)
  }

  function toggleChip(chip: string, list: string[], setter: (v: string[]) => void) {
    setter(list.includes(chip) ? list.filter((c) => c !== chip) : [...list, chip])
  }

  const instruction = buildInstruction(scope, { pace, warmth, urgency }, moodChips, toneChips)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (locked) return
    if (needsPicker && !targetId) return

    setPending(true)
    setError(null)
    try {
      await storiesApi.regenerate(storyId, {
        scope,
        target_stage: SCOPE_STAGE[scope],
        target_id: needsPicker ? targetId! : undefined,
        instruction_delta: instruction,
      })
      onRegenerated()
      // Reset sliders/chips after success
      setPace(0)
      setWarmth(0)
      setUrgency(0)
      setMoodChips([])
      setToneChips([])
    } catch (err) {
      setError(formatError(err))
    } finally {
      setPending(false)
    }
  }

  const pickerOptions = useMemo(() => {
    if (scope === 'line')
      return lines.map((l) => ({ id: l.id, label: `${l.speaker}: "${truncate(l.text)}"` }))
    if (scope === 'character')
      return characters.map((c) => ({ id: c.id, label: c.name }))
    if (scope === 'scene')
      return scenes.map((s) => ({ id: s.id, label: `Scene ${s.index + 1}: ${s.title}` }))
    return []
  }, [scope, lines, characters, scenes])

  const canSubmit =
    !locked && (!needsPicker || !!targetId)

  return (
    <section className="director-controls">
      <p className="eyebrow">Director Controls</p>
      <h2>Structured regeneration</h2>
      <p className="muted">
        Pick exactly what to change and how. Daastaan rebuilds only the stages that need it.
      </p>

      <form onSubmit={handleSubmit}>
        {/* Scope selector */}
        <fieldset className="dc-scope-group">
          <legend className="dc-legend">What to target</legend>
          {(
            [
              ['line', 'A single line'],
              ['character', "A character's lines"],
              ['scene', 'A scene'],
              ['music', 'Background score'],
              ['full_story', 'Whole episode'],
            ] as const
          ).map(([value, label]) => (
            <label key={value} className={`dc-scope-option${scope === value ? ' active' : ''}`}>
              <input
                type="radio"
                name="dc-scope"
                value={value}
                checked={scope === value}
                onChange={() => handleScopeChange(value)}
                disabled={locked}
              />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>

        {/* Target picker */}
        {needsPicker && (
          <div className="dc-target-picker">
            <label className="dc-legend">
              {scope === 'line' ? 'Which line' : scope === 'character' ? 'Which character' : 'Which scene'}
            </label>
            <select
              value={targetId ?? ''}
              onChange={(e) => setTargetId(e.target.value || null)}
              disabled={locked}
            >
              <option value="">-- Select --</option>
              {pickerOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Sliders for line/character/scene */}
        {showSliders && (
          <div className="dc-sliders">
            {(Object.keys(SLIDER_LABELS) as Array<keyof typeof SLIDER_LABELS>).map((key) => {
              const value = key === 'pace' ? pace : key === 'warmth' ? warmth : urgency
              const setter = key === 'pace' ? setPace : key === 'warmth' ? setWarmth : setUrgency
              const [lo, hi] = SLIDER_LABELS[key]
              return (
                <div key={key} className="dc-slider-row">
                  <span className="dc-slider-lo">{lo}</span>
                  <input
                    type="range"
                    min={-2}
                    max={2}
                    step={1}
                    value={value}
                    onChange={(e) => setter(Number(e.target.value))}
                    disabled={locked}
                  />
                  <span className="dc-slider-hi">{hi}</span>
                </div>
              )
            })}
          </div>
        )}

        {/* Mood chips for music */}
        {showMoodChips && (
          <div className="dc-mood-chips">
            <span className="dc-legend">Mood direction</span>
            <div className="dc-chip-row">
              {MOOD_OPTIONS.map((mood) => (
                <button
                  key={mood}
                  type="button"
                  className={`chip${moodChips.includes(mood) ? ' active' : ''}`}
                  disabled={locked}
                  onClick={() => toggleChip(mood, moodChips, setMoodChips)}
                >
                  {mood}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Tone chips for whole episode */}
        {showToneChips && (
          <div className="dc-mood-chips">
            <span className="dc-legend">Overall tone</span>
            <div className="dc-chip-row">
              {TONE_OPTIONS.map((tone) => (
                <button
                  key={tone}
                  type="button"
                  className={`chip${toneChips.includes(tone) ? ' active' : ''}`}
                  disabled={locked}
                  onClick={() => toggleChip(tone, toneChips, setToneChips)}
                >
                  {tone}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Instruction preview */}
        <div className="dc-instruction-preview">
          <span className="dc-legend">Instruction</span>
          <p className="dc-instruction-text">{instruction}</p>
        </div>

        {/* Cost preview */}
        {(!needsPicker || targetId) && (
          <div className="dc-cost-preview">
            <p>{costPreview(scope, targetId, state)}</p>
          </div>
        )}

        {error && <p className="form-error">{error}</p>}

        <button type="submit" className="btn primary" disabled={!canSubmit}>
          {pending ? 'Applying...' : 'Apply changes'}
        </button>
      </form>
    </section>
  )
}
