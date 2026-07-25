/**
 * The video editor.
 *
 * Lives in its own studio tab rather than beside the generation view: nothing
 * here runs the pipeline, and a screen full of typography controls is the wrong
 * thing to show someone who is still waiting for their episode.
 *
 * A *cut* is a saved manifest. Several can exist per story — a wide one for
 * YouTube, a vertical one for Reels — and each renders and shares independently.
 * Edits autosave, because there is no meaningful "unsaved" state for a set of
 * sliders and the alternative is a Save button nobody presses before closing the
 * tab.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { formatError, editor as editorApi } from '../../api'
import type { LocalAudio, VideoEdit, VideoEditManifest, VideoEditorBootstrap } from '../../types'
import { EditorControls } from './EditorControls'
import { EditorExport } from './EditorExport'
import { EditorPreview } from './EditorPreview'

// Long enough that dragging a slider is one request rather than forty, short
// enough that Export is never waiting on a save the user has stopped making.
const SAVE_DEBOUNCE_MS = 700

type Props = {
  storyId: string
  /** False while the tab is hidden. The preview stops, and nothing polls. */
  active: boolean
}

export function VideoEditor({ storyId, active }: Props) {
  const [data, setData] = useState<VideoEditorBootstrap | null>(null)
  const [edits, setEdits] = useState<VideoEdit[]>([])
  const [tracks, setTracks] = useState<LocalAudio[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [manifest, setManifest] = useState<VideoEditManifest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [creating, setCreating] = useState(false)

  const saveTimer = useRef<number | undefined>(undefined)
  const cancelled = useRef(false)

  useEffect(() => {
    cancelled.current = false
    return () => {
      cancelled.current = true
      if (saveTimer.current !== undefined) window.clearTimeout(saveTimer.current)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    editorApi
      .open(storyId)
      .then((next) => {
        if (cancelled.current) return
        setData(next)
        setEdits(next.edits)
        setTracks(next.local_audio)
        // Open on the newest cut that still belongs to the current version. An
        // older one is archived rather than reopened: its trim points at a
        // timeline the current episode no longer has.
        const usable = next.edits.filter((edit) => edit.version_current)
        const open = usable[usable.length - 1] ?? null
        setSelectedId(open?.id ?? null)
        setManifest(open?.manifest ?? null)
        setError(null)
      })
      .catch((err) => {
        if (!cancelled.current) setError(formatError(err))
      })
      .finally(() => {
        if (!cancelled.current) setLoading(false)
      })
  }, [storyId])

  const selected = useMemo(
    () => edits.find((edit) => edit.id === selectedId) ?? null,
    [edits, selectedId],
  )

  const mergeEdit = useCallback((next: VideoEdit) => {
    setEdits((current) =>
      current.some((edit) => edit.id === next.id)
        ? current.map((edit) => (edit.id === next.id ? next : edit))
        : [...current, next],
    )
  }, [])

  // Autosave. The manifest in state is authoritative and the response is folded
  // back in for its derived fields — `render_current` in particular, which is what
  // the Export button reads.
  const save = useCallback(
    (editId: string, next: VideoEditManifest) => {
      if (saveTimer.current !== undefined) window.clearTimeout(saveTimer.current)
      setSaving(true)
      saveTimer.current = window.setTimeout(() => {
        editorApi
          .updateCut(editId, { manifest: next })
          .then((updated) => {
            if (cancelled.current) return
            mergeEdit(updated)
            setError(null)
          })
          .catch((err) => {
            if (!cancelled.current) setError(formatError(err))
          })
          .finally(() => {
            if (!cancelled.current) setSaving(false)
          })
      }, SAVE_DEBOUNCE_MS)
    },
    [mergeEdit],
  )

  const changeManifest = useCallback(
    (next: VideoEditManifest) => {
      setManifest(next)
      if (selectedId) save(selectedId, next)
    },
    [selectedId, save],
  )

  const createCut = useCallback(
    async (base?: VideoEditManifest) => {
      setCreating(true)
      setError(null)
      try {
        const created = await editorApi.createCut(storyId, base ? { manifest: base } : {})
        mergeEdit(created)
        setSelectedId(created.id)
        setManifest(created.manifest)
      } catch (err) {
        setError(formatError(err))
      } finally {
        setCreating(false)
      }
    },
    [storyId, mergeEdit],
  )

  const removeCut = useCallback(
    async (editId: string) => {
      setError(null)
      try {
        await editorApi.deleteCut(editId)
        setEdits((current) => {
          const remaining = current.filter((edit) => edit.id !== editId)
          if (editId === selectedId) {
            const next = remaining.filter((edit) => edit.version_current).at(-1) ?? null
            setSelectedId(next?.id ?? null)
            setManifest(next?.manifest ?? null)
          }
          return remaining
        })
      } catch (err) {
        setError(formatError(err))
      }
    },
    [selectedId],
  )

  const selectCut = useCallback(
    (editId: string) => {
      const edit = edits.find((item) => item.id === editId)
      if (!edit) return
      // Flush any pending save for the cut being left, so switching away does not
      // discard the last slider move.
      if (saveTimer.current !== undefined) {
        window.clearTimeout(saveTimer.current)
        saveTimer.current = undefined
        if (selectedId && manifest) {
          void editorApi.updateCut(selectedId, { manifest }).then(mergeEdit).catch(() => undefined)
        }
        setSaving(false)
      }
      setSelectedId(edit.id)
      setManifest(edit.manifest)
    },
    [edits, selectedId, manifest, mergeEdit],
  )

  if (loading) {
    return <p className="muted loading-copy">Opening the editor…</p>
  }

  if (error && !data) {
    return <p className="form-error">{error}</p>
  }

  if (!data) return null

  if (!data.capabilities.has_artwork || data.capabilities.line_count === 0) {
    return (
      <section className="editor-panel editor-empty">
        <p className="eyebrow">Editor</p>
        <h2>Nothing to cut yet</h2>
        <p className="muted">
          The editor works from the scene artwork and the recorded lines. Once this
          episode has both, everything here becomes available — captions, framing,
          trimming, your own music, and a share link.
        </p>
      </section>
    )
  }

  const localAudioUrl =
    tracks.find((track) => track.id === manifest?.audio.local_asset_id)?.url ?? null
  const current = edits.filter((edit) => edit.version_current)
  const archived = edits.filter((edit) => !edit.version_current)

  return (
    <div className="editor">
      <header className="editor-head">
        <div>
          <p className="eyebrow">Editor</p>
          <h2>{data.title ?? 'Untitled episode'}</h2>
          <p className="muted">
            {data.capabilities.line_count} lines ·{' '}
            {(data.capabilities.duration_ms / 1000).toFixed(0)}s of finished episode
          </p>
        </div>
        {data.source_video_url && (
          <a
            className="btn ghost small"
            href={data.source_video_url}
            target="_blank"
            rel="noreferrer"
          >
            Watch the original
          </a>
        )}
      </header>

      <div className="editor-cuts">
        {current.map((edit) => (
          <button
            key={edit.id}
            type="button"
            className={`editor-chip${edit.id === selectedId ? ' active' : ''}`}
            onClick={() => selectCut(edit.id)}
          >
            {edit.name}
            <span className="editor-chip-note">{edit.manifest.aspect}</span>
          </button>
        ))}
        <button
          type="button"
          className="btn ghost small"
          disabled={creating}
          onClick={() => void createCut(manifest ?? undefined)}
        >
          {creating ? 'Adding…' : current.length === 0 ? 'Start a cut' : 'Duplicate as a new cut'}
        </button>
        {selected && (
          <button
            type="button"
            className="btn text small"
            onClick={() => void removeCut(selected.id)}
          >
            Delete this cut
          </button>
        )}
      </div>

      {archived.length > 0 && (
        <p className="muted editor-hint">
          {archived.length} {archived.length === 1 ? 'cut was' : 'cuts were'} made from an
          earlier version of this story. Their downloads still work, but they cannot be
          reopened against the current script.
        </p>
      )}

      {error && <p className="form-error">{error}</p>}

      {!selected || !manifest ? (
        <section className="editor-panel editor-empty">
          <p className="muted">
            Start a cut to trim the episode, restyle the captions, change the framing and
            lay your own music underneath.
          </p>
        </section>
      ) : (
        <div className="editor-body">
          <div className="editor-stage-column">
            <EditorPreview
              manifest={manifest}
              cues={data.cues}
              durationMs={data.capabilities.duration_ms}
              episodeAudioUrl={data.episode_audio_url}
              localAudioUrl={localAudioUrl}
              active={active}
              onTrimChange={(start, end) =>
                changeManifest({ ...manifest, trim: { start_ms: start, end_ms: end } })
              }
            />
            <EditorExport edit={selected} saving={saving} onEdit={mergeEdit} />
          </div>
          <EditorControls
            storyId={storyId}
            manifest={manifest}
            capabilities={data.capabilities}
            aspects={data.aspects}
            fonts={data.fonts}
            presets={data.caption_presets}
            tracks={tracks}
            onChange={changeManifest}
            onTracksChange={setTracks}
          />
        </div>
      )}
    </div>
  )
}
