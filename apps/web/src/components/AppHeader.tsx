type Props = {
  email: string
  onLogout: () => void
  onHome: () => void
  onCompose: () => void
  compact?: boolean
}

export function AppHeader({ email, onLogout, onHome, onCompose, compact }: Props) {
  return (
    <header className={`app-header ${compact ? 'compact' : ''}`}>
      <button type="button" className="brand-mark" onClick={onHome} aria-label="Daastaan home">
        <span className="brand-word">Daastaan</span>
      </button>
      <nav className="app-nav" aria-label="Primary">
        <button type="button" className="nav-link" onClick={onCompose}>
          New story
        </button>
        <span className="nav-email" title={email}>
          {email}
        </span>
        <button type="button" className="nav-ghost" onClick={onLogout}>
          Log out
        </button>
      </nav>
    </header>
  )
}
