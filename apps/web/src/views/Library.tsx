import { useState, useMemo, useCallback } from 'react'
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

function statusDotClass(status: string) {
  if (status === 'ready') return 'status-dot ready'
  if (status === 'generating') return 'status-dot generating'
  if (status === 'failed' || status === 'flagged') return 'status-dot failed'
  return 'status-dot'
}

const FAVORITES_KEY = 'daastaan:favorites'

function loadFavorites(): Set<string> {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY)
    if (raw) return new Set(JSON.parse(raw))
  } catch { /* ignore */ }
  return new Set()
}

function saveFavorites(favs: Set<string>) {
  localStorage.setItem(FAVORITES_KEY, JSON.stringify([...favs]))
}

function relativeTime(dateStr: string): string {
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const diffMs = now - then
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHr = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHr / 24)
  const diffWeek = Math.floor(diffDay / 7)
  const diffMonth = Math.floor(diffDay / 30)
  const diffYear = Math.floor(diffDay / 365)

  if (diffSec < 60) return 'just now'
  if (diffMin < 60) return `${diffMin} min ago`
  if (diffHr < 24) return `${diffHr} hr ago`
  if (diffDay === 1) return 'yesterday'
  if (diffDay < 7) return `${diffDay} days ago`
  if (diffWeek === 1) return '1 week ago'
  if (diffWeek < 5) return `${diffWeek} weeks ago`
  if (diffMonth === 1) return '1 month ago'
  if (diffMonth < 12) return `${diffMonth} months ago`
  if (diffYear === 1) return '1 year ago'
  return `${diffYear} years ago`
}

type FilterKey = 'all' | 'ready' | 'in_progress' | 'needs_attention' | 'favorites'
type SortKey = 'newest' | 'oldest' | 'alpha'

const FILTER_LABELS: Record<FilterKey, string> = {
  all: 'All',
  ready: 'Ready',
  in_progress: 'In Progress',
  needs_attention: 'Needs Attention',
  favorites: 'Favorites',
}

function matchesFilter(story: Story, filter: FilterKey, favorites: Set<string>): boolean {
  if (filter === 'all') return true
  if (filter === 'favorites') return favorites.has(story.id)
  if (filter === 'ready') return story.status === 'ready'
  if (filter === 'in_progress') return story.status === 'generating' || story.status === 'draft'
  if (filter === 'needs_attention') return story.status === 'failed' || story.status === 'flagged'
  return true
}

export function Library({ user, stories, onLogout, onCompose, onOpen }: Props) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<FilterKey>('all')
  const [sort, setSort] = useState<SortKey>('newest')
  const [favorites, setFavorites] = useState<Set<string>>(loadFavorites)

  const toggleFavorite = useCallback((e: React.MouseEvent, storyId: string) => {
    e.stopPropagation()
    setFavorites(prev => {
      const next = new Set(prev)
      if (next.has(storyId)) next.delete(storyId)
      else next.add(storyId)
      saveFavorites(next)
      return next
    })
  }, [])

  const filterCounts = useMemo(() => {
    const counts: Record<FilterKey, number> = {
      all: stories.length,
      ready: 0,
      in_progress: 0,
      needs_attention: 0,
      favorites: 0,
    }
    for (const s of stories) {
      if (s.status === 'ready') counts.ready++
      if (s.status === 'generating' || s.status === 'draft') counts.in_progress++
      if (s.status === 'failed' || s.status === 'flagged') counts.needs_attention++
      if (favorites.has(s.id)) counts.favorites++
    }
    return counts
  }, [stories, favorites])

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim()
    let result = stories.filter(s => {
      if (q && !(s.title ?? '').toLowerCase().includes(q)) return false
      return matchesFilter(s, filter, favorites)
    })

    result = [...result]
    if (sort === 'newest') {
      result.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    } else if (sort === 'oldest') {
      result.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    } else {
      result.sort((a, b) => (a.title ?? '').localeCompare(b.title ?? ''))
    }

    return result
  }, [stories, search, filter, sort, favorites])

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

        {stories.length > 0 && (
          <section className="library-toolbar">
            <div className="library-search-row">
              <input
                type="search"
                className="library-search"
                placeholder="Search stories..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                aria-label="Search stories"
              />
              <select
                className="library-sort"
                value={sort}
                onChange={e => setSort(e.target.value as SortKey)}
                aria-label="Sort stories"
              >
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="alpha">Alphabetical (A-Z)</option>
              </select>
            </div>
            <div className="library-filters" role="group" aria-label="Filter stories">
              {(Object.keys(FILTER_LABELS) as FilterKey[]).map(key => (
                <button
                  key={key}
                  type="button"
                  className={`filter-chip${filter === key ? ' active' : ''}`}
                  onClick={() => setFilter(key)}
                >
                  {FILTER_LABELS[key]}
                  <span className="filter-chip-count">{filterCounts[key]}</span>
                </button>
              ))}
            </div>
          </section>
        )}

        <section className="library-list" aria-label="Stories">
          {stories.length === 0 ? (
            <div className="empty-state">
              <h2>No episodes yet</h2>
              <p className="muted">Begin with a dream, a memory, or a half-finished idea.</p>
              <button type="button" className="btn secondary" onClick={onCompose}>
                Write your first scene
              </button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="empty-state">
              <h2>No matches</h2>
              <p className="muted">Try a different search term or filter.</p>
            </div>
          ) : (
            <ul>
              {filtered.map((story, index) => (
                <li key={story.id} style={{ animationDelay: `${index * 40}ms` }}>
                  <button type="button" className="story-row" onClick={() => onOpen(story.id)}>
                    <div className="story-row-left">
                      <span className={statusDotClass(story.status)} aria-hidden="true" />
                      <div>
                        <strong>{story.title ?? 'Untitled story'}</strong>
                        <span className="story-meta">
                          <span className={`status-tag ${story.status}`}>{statusLabel(story.status)}</span>
                          <span className="muted">{relativeTime(story.created_at)}</span>
                        </span>
                      </div>
                    </div>
                    <div className="story-row-right">
                      <button
                        type="button"
                        className={`story-favorite${favorites.has(story.id) ? ' active' : ''}`}
                        onClick={e => toggleFavorite(e, story.id)}
                        aria-label={favorites.has(story.id) ? 'Remove from favorites' : 'Add to favorites'}
                      >
                        {favorites.has(story.id) ? '\u2605' : '\u2606'}
                      </button>
                    </div>
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
