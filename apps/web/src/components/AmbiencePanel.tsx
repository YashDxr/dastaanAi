import { useMemo, useState } from 'react'
import type { DialogueLine, Scene } from '../types'

type SoundMode = 'narration-only' | 'narration-score' | 'full-atmosphere'

type Props = {
  scenes: Scene[]
  lines: DialogueLine[]
  hasMusicBed: boolean
}

/** Maps a scene's mood_tag and setting to a suggested ambience sound type. */
function suggestAmbience(scene: Scene): { type: string; label: string } {
  const mood = scene.mood_tag.toLowerCase()
  const setting = scene.setting.toLowerCase()

  // Setting-driven suggestions take priority.
  if (/ocean|sea|beach|coast|shore|harbor|port|pier|dock/.test(setting))
    return { type: 'ocean', label: 'Ocean waves' }
  if (/forest|wood|jungle|grove|clearing|thicket/.test(setting))
    return { type: 'forest', label: 'Forest ambience' }
  if (/rain|storm|thunder/.test(setting) || /rain|storm|thunder/.test(mood))
    return { type: 'rain', label: 'Rain & thunder' }
  if (/city|street|market|town|square|cafe|bar|pub|restaurant|shop/.test(setting))
    return { type: 'city', label: 'City soundscape' }
  if (/fire|hearth|cabin|lodge|fireplace|camp/.test(setting))
    return { type: 'fireplace', label: 'Crackling fireplace' }
  if (/cave|dungeon|underground|mine|tunnel/.test(setting))
    return { type: 'cave', label: 'Echoing cavern' }
  if (/desert|sand|dune/.test(setting))
    return { type: 'wind', label: 'Desert wind' }
  if (/mountain|cliff|peak|summit|ridge/.test(setting))
    return { type: 'wind', label: 'Mountain wind' }
  if (/castle|palace|manor|hall|throne/.test(setting))
    return { type: 'interior', label: 'Grand hall echo' }
  if (/river|creek|stream|waterfall|lake|pond/.test(setting))
    return { type: 'water', label: 'Flowing water' }
  if (/garden|meadow|field|prairie|plain/.test(setting))
    return { type: 'nature', label: 'Open meadow' }
  if (/night|dark|midnight|dusk|twilight/.test(setting) || /night|dark/.test(mood))
    return { type: 'night', label: 'Night crickets' }
  if (/space|ship|station|cockpit|bridge/.test(setting))
    return { type: 'scifi', label: 'Spaceship hum' }

  // Mood-driven fallbacks.
  if (/tense|suspense|thriller|horror|dread|anxious/.test(mood))
    return { type: 'tension', label: 'Low drone' }
  if (/romantic|love|tender|intimate/.test(mood))
    return { type: 'intimate', label: 'Soft room tone' }
  if (/happy|cheerful|joyful|playful|whimsical/.test(mood))
    return { type: 'bright', label: 'Bright atmosphere' }
  if (/sad|melancholy|somber|grief|mourning/.test(mood))
    return { type: 'somber', label: 'Quiet stillness' }

  return { type: 'ambient', label: 'Ambient tone' }
}

const MOOD_COLORS: Record<string, string> = {
  tense: '#e8a735',
  suspense: '#e8a735',
  romantic: '#d16ba5',
  sad: '#5b7db1',
  happy: '#6bbd5b',
  cheerful: '#6bbd5b',
  dark: '#5a5a6e',
  horror: '#c0392b',
  mysterious: '#8e6bbf',
  adventurous: '#e67e22',
  calm: '#48bfa0',
  dramatic: '#c0392b',
}

function moodColor(mood: string): string {
  const lower = mood.toLowerCase()
  for (const [key, color] of Object.entries(MOOD_COLORS)) {
    if (lower.includes(key)) return color
  }
  return 'var(--teal)'
}

const SOUND_MODES: { value: SoundMode; label: string; disabled: boolean; note?: string }[] = [
  { value: 'narration-only', label: 'Narration Only', disabled: false },
  { value: 'narration-score', label: 'Narration + Score', disabled: false },
  { value: 'full-atmosphere', label: 'Full Atmosphere', disabled: true, note: 'Coming soon' },
]

export function AmbiencePanel({ scenes, lines, hasMusicBed }: Props) {
  const [soundMode, setSoundMode] = useState<SoundMode>(
    hasMusicBed ? 'narration-score' : 'narration-only',
  )

  const sceneData = useMemo(() => {
    const sceneLinesMap = new Map<string, DialogueLine[]>()
    for (const line of lines) {
      const arr = sceneLinesMap.get(line.scene_id) ?? []
      arr.push(line)
      sceneLinesMap.set(line.scene_id, arr)
    }

    return scenes.map((scene) => {
      const sceneLines = sceneLinesMap.get(scene.id) ?? []
      const totalDuration = sceneLines.reduce(
        (sum, l) => sum + (l.pause_after_ms ?? 200) + 1200, // estimate ~1200ms per line
        0,
      )
      return {
        scene,
        ambience: suggestAmbience(scene),
        lineCount: sceneLines.length,
        durationMs: totalDuration,
      }
    })
  }, [scenes, lines])

  const totalDuration = sceneData.reduce((sum, d) => sum + d.durationMs, 0)

  if (scenes.length === 0) {
    return (
      <div className="ambience-panel">
        <p className="eyebrow">Sound Design</p>
        <p className="muted">Scenes will appear here once the story has been processed.</p>
      </div>
    )
  }

  return (
    <div className="ambience-panel">
      <p className="eyebrow">Sound Design</p>

      <div className="sound-mode-selector">
        {SOUND_MODES.map((mode) => (
          <button
            key={mode.value}
            type="button"
            className={`chip${soundMode === mode.value ? ' active' : ''}${mode.disabled ? ' disabled-chip' : ''}`}
            disabled={mode.disabled}
            title={mode.note}
            onClick={() => !mode.disabled && setSoundMode(mode.value)}
          >
            {mode.label}
            {mode.note && <span className="coming-soon">{mode.note}</span>}
          </button>
        ))}
      </div>

      <div className="soundscape-timeline" role="img" aria-label="Soundscape timeline">
        {sceneData.map((d) => {
          const pct = totalDuration > 0 ? (d.durationMs / totalDuration) * 100 : 100 / scenes.length
          return (
            <div
              key={d.scene.id}
              className="soundscape-timeline-block"
              style={{
                flexBasis: `${pct}%`,
                borderLeftColor: moodColor(d.scene.mood_tag),
              }}
              title={`${d.scene.title} — ${d.ambience.label}`}
            >
              <span className="soundscape-timeline-label">{d.scene.title}</span>
              <span className="soundscape-timeline-mood">{d.scene.mood_tag}</span>
            </div>
          )
        })}
      </div>

      <ul className="ambience-scene-list">
        {sceneData.map((d) => (
          <li key={d.scene.id} className="ambience-scene">
            <div className="ambience-scene-head">
              <strong>{d.scene.title}</strong>
              <span className="ambience-mood" style={{ color: moodColor(d.scene.mood_tag) }}>
                {d.scene.mood_tag}
              </span>
            </div>
            <p className="muted ambience-setting">{d.scene.setting}</p>
            <div className="ambience-suggestion">
              <span className="ambience-suggestion-icon" data-type={d.ambience.type} />
              <span>{d.ambience.label}</span>
              <span className="coming-soon">Planned</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
