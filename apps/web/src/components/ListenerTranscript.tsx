import { useEffect, useRef } from 'react'
import type { Asset, DialogueLine } from '../types'

type Props = {
  lines: DialogueLine[]
  assets: Asset[]
  activeLineId: string | null
  onSeekToLine?: (lineId: string) => void
}

export function ListenerTranscript({ lines, assets, activeLineId, onSeekToLine }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const lineRefs = useRef<Map<string, HTMLElement>>(new Map())
  const autoScrollEnabled = useRef(true)

  // Auto-scroll to the active line
  useEffect(() => {
    if (!activeLineId || !autoScrollEnabled.current) return
    const el = lineRefs.current.get(activeLineId)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
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

  const sorted = [...lines].sort((a, b) => a.index - b.index)

  if (!sorted.length) {
    return (
      <section className="listener-transcript">
        <p className="eyebrow">Transcript</p>
        <p className="muted">Transcript appears once lines are generated.</p>
      </section>
    )
  }

  // Group lines and detect scene transitions for chapter dividers
  let prevSceneId: string | null = null

  return (
    <section className="listener-transcript">
      <div className="section-head">
        <p className="eyebrow">Transcript</p>
      </div>
      <div className="listener-scroll" ref={scrollRef}>
        {sorted.map((line) => {
          const showChapter = line.scene_id !== prevSceneId
          prevSceneId = line.scene_id
          const isActive = line.id === activeLineId

          return (
            <div key={line.id}>
              {showChapter && (
                <div className="listener-chapter">
                  Scene: {line.scene_id.slice(0, 8)}
                </div>
              )}
              <article
                ref={(el) => {
                  if (el) lineRefs.current.set(line.id, el)
                  else lineRefs.current.delete(line.id)
                }}
                className={`listener-line listener-line-clickable${isActive ? ' line-active' : ''}`}
                onClick={() => onSeekToLine?.(line.id)}
              >
                <div className="listener-line-header">
                  <span className="speaker">{line.speaker}</span>
                  {line.emotion && (
                    <span className="emotion">{line.emotion}</span>
                  )}
                </div>
                <p className="line-text">{line.text}</p>
              </article>
            </div>
          )
        })}
      </div>
    </section>
  )
}
