import { useCallback, useEffect, useRef, useState } from 'react'
import { formatError, ingest as ingestApi } from '../api'
import { INGEST_METHOD_LABELS, INGEST_STATUS_LABELS } from '../types'
import type { Ingest } from '../types'

/** Extraction reports phases, not percentages, so polling is only fast enough
 *  to keep the label honest. */
const POLL_MS = 1500

/** OCR on a long scan is minutes of work. Past this the job has almost certainly
 *  died in a way that never wrote a failure row, and waiting forever is worse
 *  than telling the user to paste the text. */
const TIMEOUT_MS = 6 * 60 * 1000

const ACCEPT = '.pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.webp,.tif,.tiff'

type Props = {
  disabled?: boolean
  language?: string
  onExtracted: (result: Ingest) => void
}

export function UploadDropzone({ disabled, language, onExtracted }: Props) {
  const [job, setJob] = useState<Ingest | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const cancelled = useRef(false)

  // Reset on mount, not just set on unmount. StrictMode runs effects twice in
  // development (mount, cleanup, mount), so a flag only ever set to true stays
  // true for the component's whole life and silently discards every poll result.
  useEffect(() => {
    cancelled.current = false
    return () => {
      cancelled.current = true
    }
  }, [])

  const busy = uploading || (job !== null && job.status !== 'ready' && job.status !== 'failed')

  const poll = useCallback(
    (id: string) => {
      const deadline = Date.now() + TIMEOUT_MS
      const tick = () => {
        if (cancelled.current) return
        ingestApi
          .get(id)
          .then((next) => {
            if (cancelled.current) return
            setJob(next)
            if (next.status === 'ready' && next.cleaned_text) {
              onExtracted(next)
              return
            }
            if (next.status === 'failed') {
              setError(next.error ?? 'That file could not be read.')
              return
            }
            if (Date.now() > deadline) {
              setError('This is taking longer than expected. Try pasting the text instead.')
              return
            }
            window.setTimeout(tick, POLL_MS)
          })
          .catch((err) => {
            if (!cancelled.current) setError(formatError(err))
          })
      }
      window.setTimeout(tick, POLL_MS)
    },
    [onExtracted],
  )

  const send = useCallback(
    (file: File) => {
      setError(null)
      setJob(null)
      setUploading(true)
      ingestApi
        .upload(file, language)
        .then((res) => {
          if (cancelled.current) return
          setJob({
            id: res.ingest_id,
            filename: res.filename,
            status: 'pending',
            method: null,
            page_count: null,
            raw_chars: null,
            cleaned_text: null,
            title_hint: null,
            genre_hint: null,
            notes: null,
            error: null,
          })
          poll(res.ingest_id)
        })
        .catch((err) => {
          if (!cancelled.current) setError(formatError(err))
        })
        .finally(() => {
          if (!cancelled.current) setUploading(false)
        })
    },
    [poll, language],
  )

  const reset = () => {
    setJob(null)
    setError(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const blocked = disabled || busy

  return (
    <div className="upload">
      <div
        className={`dropzone ${dragging ? 'over' : ''} ${blocked ? 'busy' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          if (!blocked) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          if (blocked) return
          const file = e.dataTransfer.files?.[0]
          if (file) send(file)
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          disabled={blocked}
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) send(file)
          }}
        />
        <div className="dropzone-body">
          <p className="dropzone-title">
            {busy ? statusLabel(job, uploading) : 'Drop a file, or click to browse'}
          </p>
          <p className="dropzone-hint">
            {busy
              ? job?.filename ?? 'Uploading…'
              : 'PDF, Word, image or text. Scans are read with OCR.'}
          </p>
        </div>
        {busy && <span className="dropzone-spinner" aria-hidden="true" />}
      </div>

      {job?.status === 'ready' && (
        <p className="upload-result">
          <strong>{job.filename}</strong> {summarise(job)}{' '}
          <button type="button" className="linkish" onClick={reset}>
            Use a different file
          </button>
        </p>
      )}

      {error && (
        <p className="form-error">
          {error}{' '}
          <button type="button" className="linkish" onClick={reset}>
            Try again
          </button>
        </p>
      )}
    </div>
  )
}

function statusLabel(job: Ingest | null, uploading: boolean): string {
  if (uploading || !job) return 'Uploading…'
  return `${INGEST_STATUS_LABELS[job.status]}…`
}

/** Says what happened to the file, because dropping a 50,000-character upload to
 *  fit the input budget is a big enough change that the user should be told. */
function summarise(job: Ingest): string {
  const parts: string[] = []
  if (job.page_count && job.page_count > 1) parts.push(`${job.page_count} pages`)
  if (job.method) parts.push(INGEST_METHOD_LABELS[job.method] ?? job.method)

  const raw = job.raw_chars ?? 0
  const out = job.cleaned_text?.length ?? 0
  parts.push(
    raw > out * 1.2
      ? `condensed from ${raw.toLocaleString()} to ${out.toLocaleString()} characters`
      : `${out.toLocaleString()} characters`,
  )
  return `— ${parts.join(', ')}.`
}
