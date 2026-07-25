import type { Character, DialogueLine, Scene } from '../types'

type Props = {
  lines: DialogueLine[]
  characters: Character[]
  scenes: Scene[]
  onRegenerateLine?: (lineId: string) => void
  busy?: boolean
}

export function ScriptPanel({ lines, characters, scenes, onRegenerateLine, busy }: Props) {
  if (!lines.length) {
    return (
      <section className="script-panel">
        <p className="eyebrow">Script</p>
        <h2>Lines appear as the cast is written</h2>
      </section>
    )
  }

  return (
    <section className="script-panel">
      <div className="section-head">
        <div>
          <p className="eyebrow">Script</p>
          <h2>{lines.length} lines · {characters.length} voices · {scenes.length} scenes</h2>
        </div>
      </div>
      <div className="script-scroll">
        {lines.map((line) => (
          <article key={line.id} className={`script-line ${line.line_type}`}>
            <div className="line-meta">
              <span className="speaker">{line.speaker}</span>
              {line.emotion && (
                <span className="emotion">
                  {line.emotion}
                  {line.intensity ? ` · ${line.intensity}/5` : ''}
                </span>
              )}
            </div>
            <p className="line-text">{line.text}</p>
            {onRegenerateLine && (
              <button
                type="button"
                className="line-action"
                disabled={busy}
                onClick={() => onRegenerateLine(line.id)}
              >
                Respeak line
              </button>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}
