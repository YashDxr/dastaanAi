/**
 * Exporting and sharing a cut.
 *
 * An export is a worker job, so this asks for one and polls — the same shape as
 * the audio download panel, for the same reason. Sharing is deliberately gated
 * behind having exported: a link that resolves to nothing is worse than no link.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { formatError, editor as editorApi } from '../../api'
import type { VideoEdit } from '../../types'
import { RENDER_STATUS_LABELS } from '../../types'

const POLL_MS = 2000
// Rendering a long episode with a blurred backdrop is genuinely slow, and the
// worker's own ceiling is thirty minutes. Giving up earlier than it does would
// report a failure for a job that is still running.
const TIMEOUT_MS = 20 * 60 * 1000

const EXPIRY_CHOICES: [number, string][] = [
  [24, '1 day'],
  [168, '1 week'],
  [720, '30 days'],
  [0, 'No expiry'],
]

type Props = {
  edit: VideoEdit
  /** True while there are unsaved manifest changes, so Export can wait for the
   *  save rather than encoding the previous version of the cut. */
  saving: boolean
  onEdit: (edit: VideoEdit) => void
}

export function EditorExport({ edit, saving, onEdit }: Props) {
  const [error, setError] = useState<string | null>(null)
  const [sharing, setSharing] = useState(false)
  const [copied, setCopied] = useState(false)
  const [expiry, setExpiry] = useState(168)
  const cancelled = useRef(false)

  // Reset on mount as well as on unmount: StrictMode's double-invoke would
  // otherwise leave this true for the life of the component.
  useEffect(() => {
    cancelled.current = false
    return () => {
      cancelled.current = true
    }
  }, [])

  const working = edit.status === 'queued' || edit.status === 'rendering'

  // Polling is driven by status rather than started by the button, so a render
  // already in flight when the editor opens is still followed to completion.
  useEffect(() => {
    if (!working) return
    const deadline = Date.now() + TIMEOUT_MS
    let timer: number | undefined

    const poll = () => {
      editorApi
        .cut(edit.id)
        .then((next) => {
          if (cancelled.current) return
          onEdit(next)
          if (next.status === 'queued' || next.status === 'rendering') {
            if (Date.now() > deadline) {
              setError('This export is taking longer than expected. It may still finish.')
              return
            }
            timer = window.setTimeout(poll, POLL_MS)
          }
        })
        .catch((err) => {
          if (!cancelled.current) setError(formatError(err))
        })
    }

    timer = window.setTimeout(poll, POLL_MS)
    return () => window.clearTimeout(timer)
  }, [working, edit.id, onEdit])

  const render = useCallback(async () => {
    setError(null)
    try {
      onEdit(await editorApi.render(edit.id))
    } catch (err) {
      setError(formatError(err))
    }
  }, [edit.id, onEdit])

  const share = useCallback(async () => {
    setSharing(true)
    setError(null)
    try {
      await editorApi.share(edit.id, expiry)
      onEdit(await editorApi.cut(edit.id))
    } catch (err) {
      setError(formatError(err))
    } finally {
      setSharing(false)
    }
  }, [edit.id, expiry, onEdit])

  const revoke = useCallback(async () => {
    setSharing(true)
    setError(null)
    try {
      await editorApi.revokeShare(edit.id)
      onEdit(await editorApi.cut(edit.id))
    } catch (err) {
      setError(formatError(err))
    } finally {
      setSharing(false)
    }
  }, [edit.id, onEdit])

  const copy = useCallback(async () => {
    if (!edit.share_url) return
    try {
      await navigator.clipboard.writeText(edit.share_url)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard access can be refused outright. The link is on screen and
      // selectable, so this is not worth an error message.
    }
  }, [edit.share_url])

  const stale = Boolean(edit.video_url) && !edit.render_current

  return (
    <section className="editor-panel editor-export">
      <p className="eyebrow">Export and share</p>

      <p className="muted editor-status">
        {working
          ? `${RENDER_STATUS_LABELS[edit.status]}…`
          : stale
            ? 'Exported, but you have changed the cut since.'
            : RENDER_STATUS_LABELS[edit.status]}
        {edit.duration_ms && edit.render_current
          ? ` · ${(edit.duration_ms / 1000).toFixed(0)}s`
          : ''}
        {edit.size_bytes && edit.render_current ? ` · ${megabytes(edit.size_bytes)}` : ''}
      </p>

      <div className="editor-export-actions">
        <button
          type="button"
          className="btn primary"
          disabled={working || saving || (edit.render_current && !stale)}
          onClick={() => void render()}
        >
          {working
            ? 'Exporting…'
            : saving
              ? 'Saving…'
              : edit.render_current
                ? 'Exported'
                : stale
                  ? 'Export again'
                  : 'Export video'}
        </button>
        {edit.download_url && (
          <a className="btn ghost" href={edit.download_url}>
            Download{stale ? ' previous' : ''}
          </a>
        )}
      </div>

      {edit.error && <p className="form-error">{edit.error}</p>}

      {edit.video_url && (
        <div className="editor-share">
          {edit.share_url ? (
            <>
              <p className="editor-label">Anyone with this link can watch it</p>
              <div className="editor-share-link">
                <input readOnly value={edit.share_url} onFocus={(e) => e.target.select()} />
                <button type="button" className="btn ghost small" onClick={() => void copy()}>
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <p className="muted editor-share-meta">
                {edit.share_expires_at
                  ? `Expires ${new Date(edit.share_expires_at).toLocaleDateString()}`
                  : 'No expiry'}
                {edit.share_views > 0
                  ? ` · opened ${edit.share_views} ${edit.share_views === 1 ? 'time' : 'times'}`
                  : ''}
              </p>
              <button
                type="button"
                className="btn text small"
                disabled={sharing}
                onClick={() => void revoke()}
              >
                Stop sharing
              </button>
            </>
          ) : (
            <>
              <label className="editor-field">
                <span className="editor-label">Link expires after</span>
                <select
                  value={expiry}
                  onChange={(event) => setExpiry(Number(event.target.value))}
                >
                  {EXPIRY_CHOICES.map(([hours, label]) => (
                    <option key={hours} value={hours}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="btn secondary small"
                disabled={sharing || !edit.render_current}
                onClick={() => void share()}
              >
                {sharing ? 'Creating…' : 'Create a share link'}
              </button>
              {!edit.render_current && (
                <p className="muted editor-hint">
                  Export the current cut before sharing it, so the link matches what you see.
                </p>
              )}
            </>
          )}
        </div>
      )}

      {error && <p className="form-error">{error}</p>}
    </section>
  )
}

function megabytes(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
