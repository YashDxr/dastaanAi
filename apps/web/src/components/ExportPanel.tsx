import { useCallback, useEffect, useRef, useState } from 'react'
import { formatError, stories as storiesApi } from '../api'
import type { ExportFormat } from '../types'

const POLL_MS = 1500
const TIMEOUT_MS = 2 * 60 * 1000

type Props = {
  storyId: string
  /** Only meaningful once assembly has produced a mix. */
  enabled: boolean
}

export function ExportPanel({ storyId, enabled }: Props) {
  const [formats, setFormats] = useState<ExportFormat[] | null>(null)
  const [open, setOpen] = useState(false)
  const [working, setWorking] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const cancelled = useRef(false)

  // Reset on mount, not just set on unmount: StrictMode's double-invoke would
  // otherwise leave this permanently true and swallow every poll result.
  useEffect(() => {
    cancelled.current = false
    return () => {
      cancelled.current = true
    }
  }, [])

  const load = useCallback(() => {
    storiesApi
      .exports(storyId)
      .then((next) => {
        if (!cancelled.current) setFormats(next)
      })
      .catch(() => {
        // The story may not have a mix yet; the panel simply stays closed.
      })
  }, [storyId])

  useEffect(() => {
    if (open && enabled) load()
  }, [open, enabled, load])

  const download = useCallback(
    (fmt: ExportFormat) => {
      if (fmt.url) {
        window.location.assign(fmt.url)
        return
      }
      setError(null)
      setWorking(fmt.format)

      const deadline = Date.now() + TIMEOUT_MS
      const poll = () => {
        if (cancelled.current) return
        storiesApi
          .exports(storyId)
          .then((next) => {
            if (cancelled.current) return
            setFormats(next)
            const ready = next.find((f) => f.format === fmt.format && f.ready)
            if (ready?.url) {
              setWorking(null)
              window.location.assign(ready.url)
              return
            }
            if (Date.now() > deadline) {
              setWorking(null)
              setError(`Preparing the ${fmt.label} file is taking too long. Try again shortly.`)
              return
            }
            window.setTimeout(poll, POLL_MS)
          })
          .catch((err) => {
            if (cancelled.current) return
            setWorking(null)
            setError(formatError(err))
          })
      }

      storiesApi
        .requestExport(storyId, fmt.format)
        .then((res) => {
          if (cancelled.current) return
          if (res.ready && res.url) {
            setWorking(null)
            window.location.assign(res.url)
            return
          }
          window.setTimeout(poll, POLL_MS)
        })
        .catch((err) => {
          if (cancelled.current) return
          setWorking(null)
          setError(formatError(err))
        })
    },
    [storyId],
  )

  if (!enabled) return null

  return (
    <div className="export">
      <button
        type="button"
        className="btn ghost small"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? 'Hide download options' : 'Download'}
      </button>

      {open && (
        <div className="export-panel">
          <p className="export-note">
            The episode is mixed as a 128 kbps MP3. Every other format is converted from
            it, so none of them sound better than the original.
          </p>
          <ul className="export-list">
            {(formats ?? []).map((fmt) => (
              <li key={fmt.format} className={fmt.recommended ? 'recommended' : ''}>
                <div className="export-meta">
                  <span className="export-label">
                    {fmt.label}
                    {fmt.recommended && <span className="pill">Recommended</span>}
                  </span>
                  <span className="export-detail">{fmt.detail}</span>
                </div>
                <button
                  type="button"
                  className="btn ghost small"
                  disabled={working !== null}
                  onClick={() => download(fmt)}
                >
                  {working === fmt.format
                    ? 'Preparing…'
                    : fmt.ready
                      ? `Download${fmt.size_bytes ? ` (${megabytes(fmt.size_bytes)})` : ''}`
                      : 'Prepare'}
                </button>
              </li>
            ))}
            {formats === null && <li className="muted">Loading formats…</li>}
          </ul>
          {error && <p className="form-error">{error}</p>}
        </div>
      )}
    </div>
  )
}

function megabytes(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
