/**
 * Shared API surface for the user app and the admin panel.
 *
 * `schema.d.ts` is generated from FastAPI's OpenAPI document by `npm run gen:types`
 * at the repo root, with the API running. Pydantic stays the single source of
 * truth, so neither React app ever hand-writes a request or response interface.
 */

import type { paths } from './schema'

export type { paths }

/** Response body of a GET, keyed by path. */
export type GetResponse<P extends keyof paths> = paths[P] extends {
  get: { responses: { 200: { content: { 'application/json': infer R } } } }
}
  ? R
  : never

/** Request body of a POST, keyed by path. */
export type PostBody<P extends keyof paths> = paths[P] extends {
  post: { requestBody: { content: { 'application/json': infer B } } }
}
  ? B
  : never

export class ApiError extends Error {
  // Declared as fields rather than constructor parameter properties, which the
  // Vite template forbids via `erasableSyntaxOnly`.
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/**
 * Thin fetch wrapper.
 *
 * `credentials: 'include'` matters: the session is an httpOnly cookie, so the
 * browser must be told to send it. In development both apps proxy `/api` to the
 * backend, which keeps requests same-origin and means no CORS handling here.
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  // FormData must set its own Content-Type: the boundary token is generated per
  // body and is part of the header value, so overriding it makes the request
  // unparseable on the server.
  const isFormData = typeof FormData !== 'undefined' && init.body instanceof FormData

  const response = await fetch(`/api${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...(init.headers ?? {}),
    },
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      const raw = body?.detail
      if (typeof raw === 'string') detail = raw
      else if (Array.isArray(raw)) {
        detail = raw
          .map((item: { msg?: string }) => item?.msg)
          .filter(Boolean)
          .join('. ') || response.statusText
      }
    } catch {
      // Non-JSON error body; the status text is the best we have.
    }
    throw new ApiError(response.status, detail)
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}

/**
 * Live progress for a story over server-sent events.
 *
 * Preferred over the WebSocket below because it is an ordinary same-origin GET:
 * the session cookie travels with it, and the browser handles reconnection
 * itself. Callers still need a polling fallback for the case where the stream
 * cannot be established at all.
 */
export function openProgressStream(
  storyId: string,
  handlers: { onEvent: (event: unknown) => void; onOpen?: () => void; onError?: () => void },
): EventSource {
  const source = new EventSource(`/api/stories/${storyId}/events`, { withCredentials: true })
  source.onmessage = (message) => {
    try {
      handlers.onEvent(JSON.parse(message.data))
    } catch {
      // Ignore malformed frames rather than tearing down the stream.
    }
  }
  if (handlers.onOpen) source.onopen = handlers.onOpen
  if (handlers.onError) source.onerror = handlers.onError
  return source
}

/** Live progress over WebSocket. Retained for non-browser clients and as a
 * migration path; the web app uses `openProgressStream`. */
export function openProgressSocket(storyId: string, onEvent: (event: unknown) => void): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws/stories/${storyId}`)
  socket.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data))
    } catch {
      // Ignore malformed frames rather than tearing down the connection.
    }
  }
  return socket
}
