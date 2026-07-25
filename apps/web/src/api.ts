import { ApiError, apiFetch, openProgressSocket } from '@daastaan/api-types'
import type {
  DispatchAccepted,
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

export function watchProgress(
  storyId: string,
  onEvent: () => void,
): () => void {
  let socket: WebSocket | null = null
  let pollTimer: number | undefined
  let closed = false

  const startPolling = () => {
    if (pollTimer !== undefined || closed) return
    pollTimer = window.setInterval(onEvent, 2000)
  }

  try {
    socket = openProgressSocket(storyId, () => onEvent())
    socket.onopen = () => onEvent()
    socket.onerror = () => startPolling()
    socket.onclose = () => startPolling()
  } catch {
    startPolling()
  }

  startPolling()

  return () => {
    closed = true
    if (pollTimer !== undefined) window.clearInterval(pollTimer)
    socket?.close()
  }
}
