import { useCallback, useEffect, useState } from 'react'
import { auth, formatError, stories as storiesApi } from './api'
import { Landing } from './views/Landing'
import { Library } from './views/Library'
import { Compose } from './views/Compose'
import { Studio } from './views/Studio'
import { Mystery, MysteryCompose } from './views/Mystery'
import type { Story, User, View } from './types'
import './App.css'

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [stories, setStories] = useState<Story[]>([])
  const [view, setView] = useState<View>({ name: 'landing' })
  const [boot, setBoot] = useState(true)
  const [bootError, setBootError] = useState<string | null>(null)

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
      setView({ name: 'library' })
    } catch {
      setUser(null)
      setView({ name: 'landing' })
    } finally {
      setBoot(false)
    }
  }, [loadLibrary])

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  async function handleLogout() {
    try {
      await auth.logout()
    } finally {
      setUser(null)
      setStories([])
      setView({ name: 'landing' })
    }
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
          setView({ name: 'studio', storyId })
        }}
      />
    )
  }

  if (view.name === 'studio') {
    return (
      <Studio
        user={user}
        storyId={view.storyId}
        onLogout={() => void handleLogout()}
        onHome={() => {
          void loadLibrary()
          setView({ name: 'library' })
        }}
        onCompose={() => setView({ name: 'compose' })}
      />
    )
  }

  if (view.name === 'mystery-compose') return <MysteryCompose user={user} onLogout={() => void handleLogout()} onHome={() => setView({ name: 'library' })} onCreated={(storyId) => { void loadLibrary(); setView({ name: 'mystery', storyId }) }} />

  if (view.name === 'mystery') return <Mystery user={user} storyId={view.storyId} onLogout={() => void handleLogout()} onHome={() => { void loadLibrary(); setView({ name: 'library' }) }} onCompose={() => setView({ name: 'mystery-compose' })} />

  return (
    <>
      {bootError && <p className="form-error">{bootError}</p>}
      <Library
        user={user}
        stories={stories}
        onLogout={() => void handleLogout()}
        onCompose={() => setView({ name: 'compose' })}
        onMystery={() => setView({ name: 'mystery-compose' })}
        onOpen={(storyId) => setView({ name: 'studio', storyId })}
      />
    </>
  )
}
