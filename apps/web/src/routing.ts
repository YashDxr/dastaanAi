/**
 * Deep links, in the URL fragment.
 *
 * The app navigates by swapping a `View` in React state and has no router. That
 * was fine while every destination was reached by clicking through from the
 * library, but two things now have to be linkable from outside the app: the
 * editor, so "your video is ready" can point straight at it, and a shared cut,
 * which is a link sent to someone who has no account at all.
 *
 * The fragment rather than a path because it needs no server or dev-proxy rewrite
 * to survive a reload — the browser never sends it, so any host serving
 * `index.html` already handles every route here.
 */

import type { StudioTab, View } from './types'

const STUDIO_TABS: readonly StudioTab[] = ['episode', 'scenes', 'editor']

/** Ids are hex UUIDs and share tokens are urlsafe base64. Anything else is
 *  someone else's fragment, and is left alone rather than guessed at. */
const ID = /^[A-Za-z0-9_-]{1,64}$/
const TOKEN = /^[A-Za-z0-9_-]{16,128}$/

function isTab(value: string): value is StudioTab {
  return (STUDIO_TABS as readonly string[]).includes(value)
}

/**
 * The view a fragment names, or null when it names nothing this app owns.
 *
 * Null is distinct from the landing view: it means "no opinion", which is what
 * lets a first visit fall through to the ordinary bootstrap rather than being
 * forced to the landing page by an empty URL.
 */
export function parseHash(hash: string): View | null {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  if (parts.length === 0) return null

  const [head, second, third] = parts

  if (head === 'share' && second && TOKEN.test(second)) {
    return { name: 'share', token: second }
  }
  if (head === 'story' && second && ID.test(second)) {
    const tab = third && isTab(third) ? third : 'episode'
    return { name: 'studio', storyId: second, tab }
  }
  if (head === 'compose') return { name: 'compose' }
  if (head === 'library') return { name: 'library' }
  return null
}

/**
 * The fragment for a view, or null when the view should not be written to the
 * URL.
 *
 * The landing page is null because it is the unauthenticated state rather than a
 * destination: putting it in the URL would mean a signed-out reload of a story
 * link lost the story.
 */
export function hashFor(view: View): string | null {
  switch (view.name) {
    case 'share':
      return `#/share/${view.token}`
    case 'studio':
      return view.tab === 'episode'
        ? `#/story/${view.storyId}`
        : `#/story/${view.storyId}/${view.tab}`
    case 'compose':
      return '#/compose'
    case 'library':
      return '#/library'
    case 'landing':
      return null
  }
}

/** A link to one studio tab, for an anchor the user can copy or open in a new
 *  tab. Same strings `hashFor` produces, so the two cannot drift. */
export function studioLink(storyId: string, tab: StudioTab): string {
  return hashFor({ name: 'studio', storyId, tab }) ?? '#/'
}
