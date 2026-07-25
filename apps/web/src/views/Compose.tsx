import { useMemo, useState } from 'react'
import { AppHeader } from '../components/AppHeader'
import { formatError, stories as storiesApi } from '../api'
import type { User } from '../types'

const MAX_CHARS = 6000
const MIN_CHARS = 20

type Props = {
  user: User
  onLogout: () => void
  onHome: () => void
  onCreated: (storyId: string) => void
}

export function Compose({ user, onLogout, onHome, onCreated }: Props) {
  const [text, setText] = useState('')
  const [genre, setGenre] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const remaining = MAX_CHARS - text.length
  const ready = text.trim().length >= MIN_CHARS && !pending

  const hint = useMemo(() => {
    const n = text.trim().length
    if (n === 0) return 'Start with the feeling, not the plot.'
    if (n < MIN_CHARS) return `${MIN_CHARS - n} more characters to begin.`
    return 'Enough to cast. You can reshape later.'
  }, [text])

  return (
    <div className="app-frame">
      <AppHeader
        email={user.email}
        onLogout={onLogout}
        onHome={onHome}
        onCompose={() => undefined}
        compact
      />
      <main className="compose">
        <section className="compose-copy">
          <p className="eyebrow">Compose</p>
          <h1>What should we stage tonight?</h1>
          <p className="lede">
            Write freely. Daastaan will find the mood, cast the voices, and mix the episode.
          </p>
        </section>

        <form
          className="compose-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (!ready) return
            setPending(true)
            setError(null)
            void storiesApi
              .create(text.trim(), genre.trim() || undefined)
              .then((res) => onCreated(res.story_id))
              .catch((err) => setError(formatError(err)))
              .finally(() => setPending(false))
          }}
        >
          <label className="compose-label">
            Your dream or memory
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value.slice(0, MAX_CHARS))}
              rows={12}
              placeholder="I woke up still hearing the rain on the tin roof…"
              required
              minLength={MIN_CHARS}
            />
          </label>
          <div className="compose-meta">
            <p className="hint">{hint}</p>
            <p className={`char-count ${remaining < 200 ? 'warn' : ''}`}>{remaining} left</p>
          </div>

          <label className="compose-label slim">
            Genre hint <span className="optional">(optional)</span>
            <input
              value={genre}
              onChange={(e) => setGenre(e.target.value.slice(0, 60))}
              placeholder="Noir fable, quiet sci-fi, family memory…"
              maxLength={60}
            />
          </label>

          {error && <p className="form-error">{error}</p>}

          <div className="compose-actions">
            <button type="button" className="btn ghost" onClick={onHome} disabled={pending}>
              Cancel
            </button>
            <button type="submit" className="btn primary" disabled={!ready}>
              {pending ? 'Sending to studio…' : 'Generate episode'}
            </button>
          </div>
        </form>
      </main>
    </div>
  )
}
