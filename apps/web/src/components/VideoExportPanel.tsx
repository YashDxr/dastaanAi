import { useCallback, useEffect, useRef, useState } from 'react'
import { formatError, stories as storiesApi } from '../api'
import type { ExportFormat } from '../types'

// Polled less often and for far longer than the audio panels. MOV and MKV are
// remuxes that land in seconds, but WebM is a real VP9 encode of a whole
// episode, which the worker allows half an hour for — giving up after the two
// minutes the audio panels use would abandon a job that is still running.
const POLL_MS = 3000
const TIMEOUT_MS = 30 * 60 * 1000

type Props = {
  storyId: string
  /**
   * When true the format list is shown directly — no toggle button.
   * Used inside the unified Downloads section in the player.
   */
  inline?: boolean
}

export function VideoExportPanel({ storyId, inline = false }: Props) {
  const [formats, setFormats] = useState<ExportFormat[] | null>(null)
  const [open, setOpen] = useState(false)
  const [working, setWorking] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const cancelled = useRef(false)

  useEffect(() => {
    cancelled.current = false
    return () => {
      cancelled.current = true
    }
  }, [])

  const load = useCallback(() => {
    storiesApi
      .videoExports(storyId)
      .then((next) => {
        if (!cancelled.current) setFormats(next)
      })
      .catch(() => {
        // No video yet; panel stays quiet.
      })
  }, [storyId])

  useEffect(() => {
    if (inline) { load(); return }
    if (open) load()
  }, [open, inline, load])

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
          .videoExports(storyId)
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
        .requestVideoExport(storyId, fmt.format)
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

  // Inline mode: render just the format list, no toggle.
  if (inline) {
    return (
      <>
        {formats !== null && (
          <p className="export-note">
            H.264/AAC MP4 master. MOV and MKV rewrap the same streams, so only WebM is
            re-encoded — and it takes a while.
          </p>
        )}
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
          {formats === null && <li className="muted">Loading…</li>}
        </ul>
        {error && <p className="form-error">{error}</p>}
      </>
    )
  }

  // Standalone collapsible mode.
  return (
    <div className="export video-export">
      <button
        type="button"
        className="btn ghost small"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? 'Hide video downloads' : 'Download Video'}
      </button>
      {open && (
        <div className="export-panel">
          <p className="export-note">
            The pipeline renders an H.264/AAC MP4. MOV and MKV rewrap those same streams
            without touching quality; WebM is re-encoded to VP9 and is slow to prepare.
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
