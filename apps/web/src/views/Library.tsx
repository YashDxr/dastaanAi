import { AppHeader } from '../components/AppHeader'
import type { Story, User } from '../types'

type Props = {
  user: User
  stories: Story[]
  onLogout: () => void
  onCompose: () => void
  onOpen: (storyId: string) => void
}

function statusLabel(status: string) {
  if (status === 'generating') return 'In progress'
  if (status === 'ready') return 'Ready'
  if (status === 'failed') return 'Needs attention'
  if (status === 'flagged') return 'Flagged'
  return status
}

export function Library({ user, stories, onLogout, onCompose, onOpen }: Props) {
  return (
    <div className="app-frame">
      <AppHeader
        email={user.email}
        onLogout={onLogout}
        onHome={() => undefined}
        onCompose={onCompose}
      />
      <main className="library">
        <section className="library-hero">
          <p className="eyebrow">Your library</p>
          <h1>Stories waiting to be heard.</h1>
          <p className="lede">
            Open an episode to listen, follow generation live, or reshape a line without starting
            over.
          </p>
          <button type="button" className="btn primary" onClick={onCompose}>
            New story
          </button>
        </section>

        <section className="library-list" aria-label="Stories">
          {stories.length === 0 ? (
            <div className="empty-state">
              <h2>No episodes yet</h2>
              <p className="muted">Begin with a dream, a memory, or a half-finished idea.</p>
              <button type="button" className="btn secondary" onClick={onCompose}>
                Write your first scene
              </button>
            </div>
          ) : (
            <ul>
              {stories.map((story, index) => (
                <li key={story.id} style={{ animationDelay: `${index * 40}ms` }}>
                  <button type="button" className="story-row" onClick={() => onOpen(story.id)}>
                    <div>
                      <strong>{story.title ?? 'Untitled story'}</strong>
                      <span className="muted">
                        {new Date(story.created_at).toLocaleDateString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          year: 'numeric',
                        })}
                      </span>
                    </div>
                    <span className={`status-tag ${story.status}`}>{statusLabel(story.status)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  )
}
