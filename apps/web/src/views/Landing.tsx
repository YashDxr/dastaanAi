import { useState } from 'react'
import { auth, formatError } from '../api'

type Props = {
  onAuthed: () => void
}

export function Landing({ onAuthed }: Props) {
  const [mode, setMode] = useState<'login' | 'signup'>('signup')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  async function submit(form: HTMLFormElement) {
    const data = new FormData(form)
    const email = String(data.get('email') ?? '')
    const password = String(data.get('password') ?? '')
    setPending(true)
    setError(null)
    try {
      if (mode === 'login') await auth.login(email, password)
      else await auth.signup(email, password)
      onAuthed()
    } catch (err) {
      setError(formatError(err))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="landing">
      <section className="hero" aria-label="Daastaan">
        <div className="hero-media" aria-hidden>
          <div className="hero-wash" />
        </div>
        <div className="hero-copy">
          <p className="brand-lockup">Daastaan</p>
          <h1>Your dream, performed as an audio drama.</h1>
          <p className="hero-sub">
            Paste a memory. We cast voices, score the silence, and let you reshape every line in
            plain language.
          </p>
          <div className="hero-cta">
            <a href="#enter" className="btn primary">
              Begin listening
            </a>
          </div>
        </div>
      </section>

      <section id="enter" className="enter-panel">
        <div className="enter-copy">
          <p className="eyebrow">Enter the studio</p>
          <h2>{mode === 'signup' ? 'Create your account' : 'Welcome back'}</h2>
          <p className="muted">
            One account keeps your episodes, regenerations, and feedback history in one place.
          </p>
        </div>
        <form
          className="auth-form"
          onSubmit={(e) => {
            e.preventDefault()
            void submit(e.currentTarget)
          }}
        >
          <label>
            Email
            <input name="email" type="email" autoComplete="email" required placeholder="you@example.com" />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
              minLength={8}
              placeholder="At least 8 characters"
            />
          </label>
          {error && <p className="form-error">{error}</p>}
          <button type="submit" className="btn primary wide" disabled={pending}>
            {pending ? 'Please wait…' : mode === 'signup' ? 'Create account' : 'Log in'}
          </button>
          <button
            type="button"
            className="btn text"
            onClick={() => {
              setMode(mode === 'login' ? 'signup' : 'login')
              setError(null)
            }}
          >
            {mode === 'signup' ? 'Already have an account? Log in' : 'Need an account? Sign up'}
          </button>
        </form>
      </section>
    </div>
  )
}
