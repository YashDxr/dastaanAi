import { ApiError, apiFetch, openProgressStream } from '@daastaan/api-types'
import type {
  DispatchAccepted,
  FeedbackEntry,
  Progress,
  Story,
  StoryDetail,
  User,
} from './types'

export { ApiError }

export function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    return typeof err.detail === 'string' ? err.detail : 'Request failed'
  }
  if (err instanceof Error) return err.message
  return 'Something went wrong'
}

export const auth = {
  me: () => apiFetch<User>('/auth/me'),
  login: (email: string, password: string) =>
    apiFetch<User>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  signup: (email: string, password: string) =>
    apiFetch<User>('/auth/signup', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  logout: () => apiFetch<void>('/auth/logout', { method: 'POST' }),
}

export const stories = {
  list: () => apiFetch<Story[]>('/stories'),
  get: (id: string) => apiFetch<StoryDetail>(`/stories/${id}`),
  create: (raw_text: string, genre_hint?: string) =>
    apiFetch<DispatchAccepted>('/stories', {
      method: 'POST',
      body: JSON.stringify({ raw_text, genre_hint: genre_hint || null }),
    }),
  progress: (id: string) => apiFetch<Progress>(`/stories/${id}/jobs`),
  feedback: (id: string, raw_text: string) =>
    apiFetch<{ feedback_id: string; task_id: string }>(`/stories/${id}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ raw_text }),
    }),
  feedbackHistory: (id: string) => apiFetch<FeedbackEntry[]>(`/stories/${id}/feedback`),
  regenerate: (
    id: string,
    body: {
      scope: string
      target_stage: string
      target_id?: string | null
      instruction_delta?: string
    },
  ) =>
    apiFetch<DispatchAccepted>(`/stories/${id}/regenerate`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

/** How often to re-read `/jobs` when the event stream is unavailable. Slower
 *  than the old unconditional 2s poll because it is now genuinely a fallback. */
const FALLBACK_POLL_MS = 5000

/**
 * Watch a story's progress.
 *
 * SSE is the primary channel and polling only runs while the stream is down.
 * The previous version started a 2s interval unconditionally alongside the
 * socket, so it kept polling even when live updates were arriving perfectly.
 *
 * `EventSource` retries on its own, so an error is not necessarily terminal:
 * polling starts on the first failure and is cancelled again the moment the
 * stream reopens.
 */
export function watchProgress(storyId: string, onEvent: () => void): () => void {
  let source: EventSource | null = null
  let pollTimer: number | undefined
  let closed = false

  const startPolling = () => {
    if (pollTimer !== undefined || closed) return
    pollTimer = window.setInterval(onEvent, FALLBACK_POLL_MS)
  }

  const stopPolling = () => {
    if (pollTimer === undefined) return
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }

  try {
    source = openProgressStream(storyId, {
      onEvent: () => onEvent(),
      onOpen: () => {
        stopPolling()
        onEvent()
      },
      onError: startPolling,
    })
  } catch {
    startPolling()
  }

  // One immediate read so the stepper is populated before the first event, then
  // nothing further until either an event arrives or the stream fails.
  onEvent()

  return () => {
    closed = true
    stopPolling()
    source?.close()
  }
}
