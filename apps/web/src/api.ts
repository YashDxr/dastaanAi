import { ApiError, apiFetch, openProgressStream } from '@daastaan/api-types'
import type {
  DispatchAccepted,
  ConsistencyCheck,
  ExportFormat,
  FeedbackEntry,
  Ingest,
  Progress,
  Story,
  StoryDetail,
  User,
  Version,
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
  versions: (id: string) => apiFetch<Version[]>(`/stories/${id}/versions`),
  version: (id: string, versionId: string) =>
    apiFetch<StoryDetail>(`/stories/${id}/versions/${versionId}`),
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
  consistencyChecks: (id: string) =>
    apiFetch<ConsistencyCheck[]>(`/stories/${id}/consistency-checks`),
  startConsistencyCheck: (id: string) =>
    apiFetch<ConsistencyCheck>(`/stories/${id}/consistency-checks`, { method: 'POST' }),
  exports: (id: string) => apiFetch<ExportFormat[]>(`/stories/${id}/exports`),
  requestExport: (id: string, format: string) =>
    apiFetch<{ format: string; ready: boolean; url: string | null; size_bytes: number | null }>(
      `/stories/${id}/exports`,
      { method: 'POST', body: JSON.stringify({ format }) },
    ),
  regenerate: (
    id: string,
    body: {
      scope: string
      target_stage: string
      target_id?: string | null
      instruction_delta?: string
      base_version_id?: string | null
      expected_current_version_id?: string | null
    },
  ) =>
    apiFetch<DispatchAccepted>(`/stories/${id}/regenerate`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

export const ingest = {
  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return apiFetch<{ ingest_id: string; filename: string }>('/ingest', { method: 'POST', body })
  },
  get: (id: string) => apiFetch<Ingest>(`/ingest/${id}`),
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
