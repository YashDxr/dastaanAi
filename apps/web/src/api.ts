import { ApiError, apiFetch, openProgressStream } from '@daastaan/api-types'
import type { ProgressEvent } from '@daastaan/api-types'
import { needsRefresh } from './live'
import type {
  CliffhangerAnalysis,
  DispatchAccepted,
  ExportFormat,
  FeedbackEntry,
  Ingest,
  Progress,
  Story,
  StoryDetail,
  StoryGenomeAnalysis,
  User,
  WritersRoomSession,
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
  create: (raw_text: string, genre_hint?: string, output_format?: string, language?: string) =>
    apiFetch<DispatchAccepted>('/stories', {
      method: 'POST',
      body: JSON.stringify({ raw_text, genre_hint: genre_hint || null, output_format: output_format || 'audio', language: language || 'en' }),
    }),
  progress: (id: string) => apiFetch<Progress>(`/stories/${id}/jobs`),
  feedback: (id: string, raw_text: string) =>
    apiFetch<{ feedback_id: string; task_id: string }>(`/stories/${id}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ raw_text }),
    }),
  feedbackHistory: (id: string) => apiFetch<FeedbackEntry[]>(`/stories/${id}/feedback`),
  exports: (id: string) => apiFetch<ExportFormat[]>(`/stories/${id}/exports`),
  requestExport: (id: string, format: string) =>
    apiFetch<{ format: string; ready: boolean; url: string | null; size_bytes: number | null }>(
      `/stories/${id}/exports`,
      { method: 'POST', body: JSON.stringify({ format }) },
    ),
  bgmExports: (id: string) => apiFetch<ExportFormat[]>(`/stories/${id}/bgm/exports`),
  requestBgmExport: (id: string, format: string) =>
    apiFetch<{ format: string; ready: boolean; url: string | null; size_bytes: number | null }>(
      `/stories/${id}/bgm/exports`,
      { method: 'POST', body: JSON.stringify({ format }) },
    ),
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
  // --- Post-production analysis features ---
  writersRoom: (id: string) =>
    apiFetch<WritersRoomSession[]>(`/stories/${id}/writers-room`),
  startWritersRoom: (id: string) =>
    apiFetch<WritersRoomSession>(`/stories/${id}/writers-room`, { method: 'POST' }),
  cliffhanger: (id: string) =>
    apiFetch<CliffhangerAnalysis[]>(`/stories/${id}/cliffhanger`),
  startCliffhanger: (id: string) =>
    apiFetch<CliffhangerAnalysis>(`/stories/${id}/cliffhanger`, { method: 'POST' }),
  genome: (id: string) =>
    apiFetch<StoryGenomeAnalysis[]>(`/stories/${id}/genome`),
  startGenome: (id: string) =>
    apiFetch<StoryGenomeAnalysis>(`/stories/${id}/genome`, { method: 'POST' }),
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

/** How long to gather content-changing events before refetching.
 *
 *  A fan-out lands dozens of assets in a burst, and each one used to trigger its own
 *  three requests. Coalescing them costs a fraction of a second of staleness on
 *  content the user is not looking at yet, and the progress bar does not wait for
 *  any of it — that now moves on the event itself. */
const REFRESH_COALESCE_MS = 400

type Watchers = {
  /** Applied immediately, on every frame. This is what makes the UI feel live. */
  onEvent: (event: ProgressEvent) => void
  /** Called when authoritative state should be re-read. Already coalesced. */
  onRefresh: () => void
}

/**
 * Watch a story's progress.
 *
 * SSE is the primary channel and polling only runs while the stream is down. The
 * events themselves now drive the UI: the previous version discarded every payload
 * and refetched `/jobs` instead, so progress could only move as fast as a round trip
 * and could only ever be as detailed as the `jobs` table.
 *
 * `EventSource` retries on its own, so an error is not necessarily terminal: polling
 * starts on the first failure and is cancelled again the moment the stream reopens.
 * Reconnection resumes from the last event this client saw, so nothing published
 * during the gap is lost.
 */
export function watchProgress(storyId: string, watchers: Watchers): () => void {
  let source: EventSource | null = null
  let pollTimer: number | undefined
  let refreshTimer: number | undefined
  let closed = false

  const startPolling = () => {
    if (pollTimer !== undefined || closed) return
    pollTimer = window.setInterval(watchers.onRefresh, FALLBACK_POLL_MS)
  }

  const stopPolling = () => {
    if (pollTimer === undefined) return
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }

  const scheduleRefresh = () => {
    if (closed || refreshTimer !== undefined) return
    refreshTimer = window.setTimeout(() => {
      refreshTimer = undefined
      watchers.onRefresh()
    }, REFRESH_COALESCE_MS)
  }

  try {
    source = openProgressStream(storyId, {
      onEvent: (event) => {
        watchers.onEvent(event)
        if (needsRefresh(event)) scheduleRefresh()
      },
      onOpen: () => {
        stopPolling()
        // The stream replays the run so far, but only progress events - the story
        // content itself has to come from REST.
        watchers.onRefresh()
      },
      onError: startPolling,
    })
  } catch {
    startPolling()
  }

  // One immediate read so the stepper is populated before the first event, then
  // nothing further until either an event arrives or the stream fails.
  watchers.onRefresh()

  return () => {
    closed = true
    stopPolling()
    if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
    source?.close()
  }
}
