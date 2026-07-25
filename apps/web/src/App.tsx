import { apiFetch, ApiError } from '@daastaan/api-types'
import { useEffect, useState } from 'react'
import './App.css'

type User = { id: string; email: string; role: string }
type Story = { id: string; title: string | null; status: string; current_version_id: string | null }

/**
 * Starting shell for the listener-facing app.
 *
 * Deliberately minimal: it proves the auth cookie round-trip and the story list
 * work end to end, which is the piece that is annoying to debug later. The
 * studio view, progress stepper, and player build on top of this.
 */
export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [stories, setStories] = useState<Story[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch<User>('/auth/me')
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!user) return
    apiFetch<Story[]>('/stories')
      .then(setStories)
      .catch((err: ApiError) => setError(err.detail))
  }, [user])

  async function handleAuth(event: React.FormEvent<HTMLFormElement>, path: '/auth/login' | '/auth/signup') {
    event.preventDefault()
    setError(null)
    const form = new FormData(event.currentTarget)
    try {
      const me = await apiFetch<User>(path, {
        method: 'POST',
        body: JSON.stringify({ email: form.get('email'), password: form.get('password') }),
      })
      setUser(me)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'something went wrong')
    }
  }

  if (loading) return <main className="shell">Loading…</main>

  if (!user) {
    return (
      <main className="shell">
        <h1>Daastaan</h1>
        <p className="tagline">Turn a dream into an audio drama.</p>
        <form onSubmit={(e) => handleAuth(e, '/auth/login')}>
          <input name="email" type="email" placeholder="you@example.com" required />
          <input name="password" type="password" placeholder="password" minLength={8} required />
          <div className="row">
            <button type="submit">Log in</button>
            <button type="button" onClick={(e) => handleAuth(e as never, '/auth/signup')}>
              Sign up
            </button>
          </div>
        </form>
        {error && <p className="error">{error}</p>}
      </main>
    )
  }

  return (
    <main className="shell">
      <header className="row">
        <h1>Daastaan</h1>
        <button
          onClick={async () => {
            await apiFetch('/auth/logout', { method: 'POST' })
            setUser(null)
          }}
        >
          Log out
        </button>
      </header>
      <p className="tagline">Signed in as {user.email}</p>

      <h2>Your stories</h2>
      {stories.length === 0 ? (
        <p>No stories yet. Paste a dream to get started.</p>
      ) : (
        <ul className="stories">
          {stories.map((story) => (
            <li key={story.id}>
              <strong>{story.title ?? 'Untitled'}</strong>
              <span className={`badge ${story.status}`}>{story.status}</span>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="error">{error}</p>}
    </main>
  )
}
