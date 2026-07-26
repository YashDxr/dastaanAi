import { useEffect, useMemo, useRef } from 'react'
import { CharacterAvatar } from './CharacterAvatar'
import type { Character, DialogueLine, Scene } from '../types'

type Props = {
  lines: DialogueLine[]
  characters: Character[]
  scenes: Scene[]
  onRegenerateLine?: (lineId: string) => void
  onSeekLine?: (lineId: string) => void
  activeLineId?: string | null
  busy?: boolean
  avatarUrls?: Map<string, string>
}

export function ScriptPanel({
  lines,
  characters,
  scenes,
  onRegenerateLine,
  onSeekLine,
  activeLineId,
  busy,
  avatarUrls,
}: Props) {
  const charByIdOrName = useMemo(() => {
    const map = new Map<string, Character>()
    for (const c of characters) {
      map.set(c.id, c)
      map.set(c.name, c)
    }
    return map
  }, [characters])

  const scrollRef = useRef<HTMLDivElement>(null)
  const lineRefs = useRef<Map<string, HTMLElement>>(new Map())
  const autoScrollEnabled = useRef(true)

  // Auto-scroll to the active line
  useEffect(() => {
    if (!activeLineId || !autoScrollEnabled.current) return
    const el = lineRefs.current.get(activeLineId)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [activeLineId])

  // Re-enable auto-scroll when the active line changes
  const prevActiveLine = useRef(activeLineId)
  useEffect(() => {
    if (activeLineId !== prevActiveLine.current) {
      prevActiveLine.current = activeLineId
      autoScrollEnabled.current = true
    }
  }, [activeLineId])

  // Detect manual scroll: temporarily disable auto-scroll, re-enable after 4s idle
  useEffect(() => {
    const container = scrollRef.current
    if (!container) return
    let timeoutId: ReturnType<typeof setTimeout>
    const onScroll = () => {
      autoScrollEnabled.current = false
      clearTimeout(timeoutId)
      timeoutId = setTimeout(() => { autoScrollEnabled.current = true }, 4000)
    }
    container.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      container.removeEventListener('scroll', onScroll)
      clearTimeout(timeoutId)
    }
  }, [])

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
      <div className="script-scroll" ref={scrollRef}>
        {lines.map((line) => {
          const char = charByIdOrName.get(line.character_id ?? '') ?? charByIdOrName.get(line.speaker)
          const isActive = line.id === activeLineId
          return (
            <article
              key={line.id}
              ref={(el) => {
                if (el) lineRefs.current.set(line.id, el)
                else lineRefs.current.delete(line.id)
              }}
              className={`script-line ${line.line_type}${isActive ? ' active' : ''}`}
              onClick={() => onSeekLine?.(line.id)}
              style={onSeekLine ? { cursor: 'pointer' } : undefined}
            >
              <div className="line-meta">
                {char && <CharacterAvatar name={char.name} role={char.role} size="sm" imageUrl={avatarUrls?.get(char.id)} />}
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
                  onClick={(e) => {
                    e.stopPropagation()
                    onRegenerateLine(line.id)
                  }}
                >
                  Respeak line
                </button>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
