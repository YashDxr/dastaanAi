import { useCallback, useEffect, useRef, useState } from 'react'
import { auth, formatError, stories as storiesApi } from './api'
import { hashFor, parseHash } from './routing'
import { Landing } from './views/Landing'
import { Library } from './views/Library'
import { Compose } from './views/Compose'
import { Share } from './views/Share'
import { Studio } from './views/Studio'
import type { Story, StudioTab, User, View } from './types'
import './App.css'

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [stories, setStories] = useState<Story[]>([])
  const [view, setView] = useState<View>(
    () => parseHash(window.location.hash) ?? { name: 'landing' },
  )
  const [boot, setBoot] = useState(true)
  const [bootError, setBootError] = useState<string | null>(null)
  // Where the URL asked to go. Consumed once, by the first successful bootstrap,
  // so signing out and back in later lands on the library rather than jumping
  // back to whatever was linked hours ago.
  const deepLink = useRef<View | null>(parseHash(window.location.hash))

  const loadLibrary = useCallback(async () => {
    const list = await storiesApi.list()
    setStories(list)
  }, [])

  const bootstrap = useCallback(async () => {
    setBoot(true)
    setBootError(null)
    try {
      const me = await auth.me()
      setUser(me)
      await loadLibrary()
      const target = deepLink.current
      deepLink.current = null
      // A deep link to a story or the compose form is honoured; anything else
      // (including the share view, which never gets here) falls to the library.
      setView(
        target && (target.name === 'studio' || target.name === 'compose')
          ? target
          : { name: 'library' },
      )
    } catch {
      setUser(null)
      deepLink.current = null
      setView({ name: 'landing' })
    } finally {
      setBoot(false)
    }
  }, [loadLibrary])

  // Once, on mount. Guarded by a ref rather than an empty dependency list so the
  // view can be read without navigation re-running the whole bootstrap.
  const booted = useRef(false)
  useEffect(() => {
    if (booted.current) return
    booted.current = true
    // A shared cut needs no session, so it must not wait on one — and the failed
    // `auth.me` behind it must not send the viewer to the landing page.
    if (view.name === 'share') {
      setBoot(false)
      return
    }
    void bootstrap()
  }, [bootstrap, view.name])

  // Keep the URL in step with the view. `replaceState` rather than assigning to
  // `location.hash`: assigning fires `hashchange`, which the listener below would
  // read back and turn into a second render of the state we just set.
  useEffect(() => {
    const next = hashFor(view)
    if (next && window.location.hash !== next) {
      window.history.replaceState(null, '', next)
    }
  }, [view])

  // Back and forward. Only fires for a hash the user or the browser changed,
  // never for one this app wrote.
  useEffect(() => {
    const onHashChange = () => {
      const parsed = parseHash(window.location.hash)
      if (parsed) setView(parsed)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  async function handleLogout() {
    try {
      await auth.logout()
    } finally {
      setUser(null)
      setStories([])
      // The landing view writes no fragment, so a story link left in the URL would
      // survive the sign-out and be followed on the next reload.
      window.history.replaceState(null, '', window.location.pathname)
      setView({ name: 'landing' })
    }
  }

  if (view.name === 'share') {
    return (
      <Share
        token={view.token}
        onEnter={() => {
          window.history.replaceState(null, '', window.location.pathname)
          setView({ name: 'landing' })
        }}
      />
    )
  }

  if (boot) {
    return (
      <div className="app-frame">
        <main className="library">
          <p className="brand-word">Daastaan</p>
          <p className="muted">Opening the studio…</p>
        </main>
      </div>
    )
  }

  if (!user || view.name === 'landing') {
    return (
      <Landing
        onAuthed={() => {
          void bootstrap().catch((err) => setBootError(formatError(err)))
        }}
      />
    )
  }

  if (view.name === 'compose') {
    return (
      <Compose
        user={user}
        onLogout={() => void handleLogout()}
        onHome={() => setView({ name: 'library' })}
        onCreated={(storyId) => {
          void loadLibrary()
          setView({ name: 'studio', storyId, tab: 'episode' })
        }}
      />
    )
  }

  if (view.name === 'studio') {
    const { storyId, tab } = view
    return (
      <Studio
        user={user}
        storyId={storyId}
        tab={tab}
        onTab={(next: StudioTab) => setView({ name: 'studio', storyId, tab: next })}
        onLogout={() => void handleLogout()}
        onHome={() => {
          void loadLibrary()
          setView({ name: 'library' })
        }}
        onCompose={() => setView({ name: 'compose' })}
      />
    )
  }

  return (
    <>
      {bootError && <p className="form-error">{bootError}</p>}
      <Library
        user={user}
        stories={stories}
        onLogout={() => void handleLogout()}
        onCompose={() => setView({ name: 'compose' })}
        onOpen={(storyId) => setView({ name: 'studio', storyId, tab: 'episode' })}
      />
    </>
  )
}
