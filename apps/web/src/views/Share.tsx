/**
 * A shared cut, viewed by someone who may have no account.
 *
 * The only view that renders before the session check, so it is written to need
 * nothing from one: no library, no header with a sign-out button, no story text.
 * A recipient sees a clip and an invitation, which is all a link should give away.
 */

import { useEffect, useState } from 'react'
import { formatError, share as shareApi } from '../api'
import type { SharedCut } from '../types'

type Props = {
  token: string
  /** Leaves the shared view for the app proper. */
  onEnter: () => void
}

export function Share({ token, onEnter }: Props) {
  const [cut, setCut] = useState<SharedCut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    shareApi
      .get(token)
      .then((next) => {
        if (!cancelled) setCut(next)
      })
      .catch((err) => {
        if (!cancelled) setError(formatError(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token])

  return (
    <div className="app-frame">
      <main className="share-view">
        <p className="brand-word">Daastaan</p>

        {loading && <p className="muted">Loading…</p>}

        {!loading && error && (
          <section className="share-card">
            <h1>This link is not available</h1>
            <p className="muted">
              It may have expired, or been withdrawn by whoever shared it.
            </p>
            <button type="button" className="btn primary" onClick={onEnter}>
              Make your own
            </button>
          </section>
        )}

        {cut && (
          <section className="share-card">
            <p className="eyebrow">{cut.name}</p>
            <h1>{cut.title ?? 'An untitled story'}</h1>
            <video
              className="share-video"
              src={cut.video_url}
              controls
              playsInline
              preload="metadata"
              style={{ aspectRatio: cut.aspect.replace(':', ' / ') }}
            />
            <div className="share-actions">
              <a className="btn ghost" href={`${cut.video_url}?download=1`}>
                Download
              </a>
              <button type="button" className="btn primary" onClick={onEnter}>
                Turn your own story into this
              </button>
            </div>
            {cut.expires_at && (
              <p className="muted share-expiry">
                This link expires {new Date(cut.expires_at).toLocaleDateString()}.
              </p>
            )}
          </section>
        )}
      </main>
    </div>
  )
}
