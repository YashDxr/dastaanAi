/**
 * The preview.
 *
 * Not a preview of the exported file — there is no exported file until someone
 * asks for one. It is the same source material the renderer uses (the scene
 * artwork, the episode mix) composited in the browser, so a change to a font or a
 * margin shows up instantly instead of costing an encode. Styling is therefore
 * genuinely WYSIWYG; motion and crossfades are not, and are marked as such.
 *
 * Two clocks, because a cut can begin with a title card that no audio covers.
 * During the card the playhead is driven by wall time; after it the audio element
 * is the clock, which is the only way to stay in sync with a mix the browser is
 * decoding itself.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CaptionCue, VideoEditManifest } from '../../types'
import { aspectCss, captionCss, captionPlacement, gainToVolume } from './captionCss'

// The playhead is state, and state at 60 Hz re-renders the whole editor for
// changes nobody can see. Captions and artwork change on the order of seconds.
const TICK_MS = 60

type Props = {
  manifest: VideoEditManifest
  cues: CaptionCue[]
  /** Total length of the untrimmed episode. */
  durationMs: number
  episodeAudioUrl: string | null
  /** Resolved from `manifest.audio.local_asset_id`, or null when none is chosen. */
  localAudioUrl: string | null
  /** False while the editor tab is hidden. Playback stops rather than continuing
   *  behind a tab the user has left. */
  active: boolean
  onTrimChange: (start: number, end: number | null) => void
}

export function EditorPreview({
  manifest,
  cues,
  durationMs,
  episodeAudioUrl,
  localAudioUrl,
  active,
  onTrimChange,
}: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const localRef = useRef<HTMLAudioElement | null>(null)
  const frameRef = useRef<HTMLDivElement | null>(null)
  const rafRef = useRef<number | undefined>(undefined)
  const cardAnchorRef = useRef(0)
  const lastPushedRef = useRef(0)

  const [playing, setPlaying] = useState(false)
  const [cutTime, setCutTime] = useState(0)
  const [frameWidth, setFrameWidth] = useState(640)

  const trimStart = manifest.trim.start_ms
  const trimEnd = manifest.trim.end_ms ?? durationMs
  const leadMs = manifest.title_card.enabled ? manifest.title_card.duration_ms : 0
  const cutDuration = Math.max(leadMs + (trimEnd - trimStart), 1)

  // Everything the animation loop reads lives in a ref as well. The loop is
  // started once per play and would otherwise close over the trim and gain values
  // that happened to be current when the user pressed play.
  const live = useRef({ trimStart, trimEnd, leadMs, cutDuration, manifest })
  live.current = { trimStart, trimEnd, leadMs, cutDuration, manifest }

  const stop = useCallback(() => {
    setPlaying(false)
    if (rafRef.current !== undefined) cancelAnimationFrame(rafRef.current)
    rafRef.current = undefined
    audioRef.current?.pause()
    localRef.current?.pause()
  }, [])

  // The preview keeps its own scale factor: `size_pt` and the margins are pixels
  // at the delivery resolution, and the frame on screen is a fraction of that.
  useEffect(() => {
    const node = frameRef.current
    if (!node || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => {
      setFrameWidth(entry.contentRect.width || 640)
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!active) stop()
  }, [active, stop])

  // A trim that moves under the playhead would otherwise leave it outside the cut,
  // showing a frame the export does not contain.
  useEffect(() => {
    setCutTime((current) => Math.min(current, cutDuration))
  }, [cutDuration])

  const syncLocal = useCallback((cutMs: number) => {
    const local = localRef.current
    const mix = live.current.manifest.audio
    if (!local) return
    const want = (cutMs - mix.local_offset_ms) / 1000
    const length = Number.isFinite(local.duration) ? local.duration : 0
    if (want < 0 || (!mix.local_loop && length > 0 && want > length)) {
      if (!local.paused) local.pause()
      return
    }
    const target = mix.local_loop && length > 0 ? want % length : want
    // Only correct real drift. Assigning `currentTime` every frame makes the
    // element re-seek continuously, which stutters far worse than being 100ms out.
    if (Math.abs(local.currentTime - target) > 0.25) local.currentTime = target
    if (local.paused) void local.play().catch(() => undefined)
  }, [])

  const push = useCallback((next: number) => {
    if (Math.abs(next - lastPushedRef.current) < TICK_MS) return
    lastPushedRef.current = next
    setCutTime(next)
  }, [])

  const tick = useCallback(
    (now: number) => {
      const { leadMs: lead, trimStart: from, trimEnd: to, cutDuration: total } = live.current
      const audio = audioRef.current

      const elapsed = now - cardAnchorRef.current
      if (elapsed < lead) {
        push(elapsed)
        syncLocal(elapsed)
        rafRef.current = requestAnimationFrame(tick)
        return
      }

      if (!audio) {
        // No mix to play: the card is all there is, so run the clock out on wall
        // time and stop at the end rather than hanging on the last frame.
        if (elapsed >= total) {
          setCutTime(total)
          stop()
          return
        }
        push(elapsed)
        rafRef.current = requestAnimationFrame(tick)
        return
      }

      if (audio.paused) {
        audio.currentTime = from / 1000
        void audio.play().catch(() => stop())
      }

      const audioMs = audio.currentTime * 1000
      if (audioMs >= to) {
        setCutTime(total)
        stop()
        return
      }
      const next = lead + Math.max(0, audioMs - from)
      push(next)
      syncLocal(next)
      rafRef.current = requestAnimationFrame(tick)
    },
    [push, stop, syncLocal],
  )

  const play = useCallback(() => {
    const audio = audioRef.current
    const start = cutTime >= cutDuration - TICK_MS ? 0 : cutTime
    cardAnchorRef.current = performance.now() - start
    lastPushedRef.current = start
    setCutTime(start)
    setPlaying(true)

    if (audio && start >= leadMs) {
      audio.currentTime = (trimStart + (start - leadMs)) / 1000
      void audio.play().catch(() => stop())
    }
    rafRef.current = requestAnimationFrame(tick)
  }, [cutTime, cutDuration, leadMs, trimStart, tick, stop])

  const seek = useCallback(
    (next: number) => {
      const clamped = Math.min(Math.max(next, 0), cutDuration)
      cardAnchorRef.current = performance.now() - clamped
      lastPushedRef.current = clamped
      setCutTime(clamped)
      const audio = audioRef.current
      if (audio) {
        audio.currentTime = (trimStart + Math.max(0, clamped - leadMs)) / 1000
        if (clamped < leadMs && !audio.paused) audio.pause()
      }
      syncLocal(clamped)
    },
    [cutDuration, leadMs, trimStart, syncLocal],
  )

  useEffect(() => stop, [stop])

  // Gains are applied to the elements rather than re-read in the loop so a slider
  // is audible while the preview is already running.
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = gainToVolume(manifest.audio.narration_gain_db)
    }
  }, [manifest.audio.narration_gain_db])

  useEffect(() => {
    if (localRef.current) {
      localRef.current.volume = gainToVolume(manifest.audio.local_gain_db)
    }
  }, [manifest.audio.local_gain_db, localAudioUrl])

  const episodeMs = trimStart + Math.max(0, cutTime - leadMs)
  const onCard = cutTime < leadMs
  const scale = frameWidth / 1280

  const current = useMemo(
    () => cues.find((cue) => episodeMs >= cue.start_ms && episodeMs < cue.end_ms) ?? null,
    [cues, episodeMs],
  )
  const upcoming = useMemo(() => {
    if (!current) return []
    const at = cues.indexOf(current)
    return cues.slice(at + 1, at + 3)
  }, [cues, current])

  const captionText = useMemo(() => {
    if (!current || !manifest.caption.enabled) return ''
    if (episodeMs >= current.caption_end_ms) return ''
    const raw = manifest.caption_overrides[current.line_id] ?? current.text
    const body = manifest.caption.uppercase ? raw.toUpperCase() : raw
    if (!body.trim()) return ''
    if (!manifest.caption.show_speaker || !current.speaker) return body
    const speaker = manifest.caption.uppercase ? current.speaker.toUpperCase() : current.speaker
    return `${speaker}: ${body}`
  }, [current, episodeMs, manifest.caption, manifest.caption_overrides])

  const wrapped = useMemo(
    () => wrapText(captionText, manifest.caption.max_chars_per_line),
    [captionText, manifest.caption.max_chars_per_line],
  )

  return (
    <section className="editor-preview">
      <div className="editor-preview-head">
        <p className="eyebrow">Preview</p>
        <span className="muted editor-preview-note">
          Type, colour and framing are exact. Motion and crossfades are added when you export.
        </span>
      </div>

      <div
        className="editor-frame"
        ref={frameRef}
        style={{ aspectRatio: aspectCss(manifest.aspect) }}
      >
        {onCard ? (
          <div className="editor-card">
            {manifest.title_card.heading.trim() && (
              <p className="editor-card-heading">{manifest.title_card.heading}</p>
            )}
            {manifest.title_card.subheading.trim() && (
              <p className="editor-card-sub">{manifest.title_card.subheading}</p>
            )}
          </div>
        ) : current?.image_url ? (
          <>
            {manifest.frame_fill === 'blur' && (
              <img
                className="editor-frame-backdrop"
                src={current.image_url}
                alt=""
                aria-hidden="true"
              />
            )}
            <img
              className={`editor-frame-image ${manifest.frame_fill}`}
              src={current.image_url}
              alt={current.text.slice(0, 120)}
            />
          </>
        ) : (
          <p className="muted editor-frame-empty">No artwork for this moment.</p>
        )}

        {!onCard && wrapped.length > 0 && (
          <div
            className="editor-caption-layer"
            style={captionPlacement(manifest.caption, scale)}
          >
            <span className="editor-caption" style={captionCss(manifest.caption, scale)}>
              {wrapped.map((line, index) => (
                <span key={index} className="editor-caption-line">
                  {line}
                </span>
              ))}
            </span>
          </div>
        )}

        {manifest.watermark.enabled && manifest.watermark.text.trim() && (
          <span
            className={`editor-watermark ${manifest.watermark.position}`}
            style={{
              opacity: manifest.watermark.opacity,
              fontSize: `${Math.max(0.028 * 720 * scale, 8)}px`,
            }}
          >
            {manifest.watermark.text}
          </span>
        )}

        {/* Warmed so the next frame does not flash empty while it loads. */}
        <div className="editor-preload" aria-hidden="true">
          {upcoming.map((cue) =>
            cue.image_url ? <img key={cue.line_id} src={cue.image_url} alt="" /> : null,
          )}
        </div>
      </div>

      <div className="editor-transport">
        <button
          type="button"
          className="btn primary small"
          onClick={() => (playing ? stop() : play())}
          disabled={!episodeAudioUrl && leadMs === 0}
        >
          {playing ? 'Pause' : 'Play'}
        </button>
        <input
          type="range"
          min={0}
          max={cutDuration}
          step={100}
          value={Math.round(cutTime)}
          aria-label="Playhead"
          onChange={(event) => seek(Number(event.target.value))}
        />
        <span className="editor-time">
          {clock(cutTime)} / {clock(cutDuration)}
        </span>
      </div>

      <TrimStrip
        cues={cues}
        durationMs={durationMs}
        trimStart={trimStart}
        trimEnd={trimEnd}
        episodeMs={episodeMs}
        onSeekEpisode={(ms) => seek(leadMs + Math.max(0, ms - trimStart))}
        onTrimChange={onTrimChange}
      />

      {episodeAudioUrl && (
        <audio ref={audioRef} src={episodeAudioUrl} preload="metadata" hidden />
      )}
      {localAudioUrl && (
        <audio
          ref={localRef}
          src={localAudioUrl}
          preload="metadata"
          loop={manifest.audio.local_loop}
          hidden
        />
      )}
    </section>
  )
}

/**
 * The line strip, and the trim controls that act on it.
 *
 * Trimming is done against line boundaries rather than with free-dragging
 * handles, because that is what people actually want from a story: "start at this
 * line" and "end after this one". It also means a trim can never land mid-word,
 * which a pixel-accurate handle on a 40-minute timeline does constantly.
 */
function TrimStrip({
  cues,
  durationMs,
  trimStart,
  trimEnd,
  episodeMs,
  onSeekEpisode,
  onTrimChange,
}: {
  cues: CaptionCue[]
  durationMs: number
  trimStart: number
  trimEnd: number
  episodeMs: number
  onSeekEpisode: (ms: number) => void
  onTrimChange: (start: number, end: number | null) => void
}) {
  const trimmed = trimStart > 0 || trimEnd < durationMs
  const scenes = useMemo(() => groupScenes(cues), [cues])

  return (
    <div className="editor-trim">
      <div className="editor-trim-head">
        <p className="eyebrow">Timeline</p>
        <span className="muted">
          {trimmed
            ? `Keeping ${clock(trimEnd - trimStart)} of ${clock(durationMs)}`
            : `Full episode · ${clock(durationMs)}`}
        </span>
      </div>

      <div className="editor-trim-track" role="group" aria-label="Lines">
        {cues.map((cue) => {
          const included = cue.end_ms > trimStart && cue.start_ms < trimEnd
          return (
            <button
              key={cue.line_id}
              type="button"
              className={`editor-trim-block${included ? '' : ' excluded'}${
                episodeMs >= cue.start_ms && episodeMs < cue.end_ms ? ' current' : ''
              }`}
              style={{ flexGrow: Math.max(cue.end_ms - cue.start_ms, 1) }}
              title={`${cue.speaker ? `${cue.speaker}: ` : ''}${cue.text}`}
              aria-label={`Line ${cue.index + 1}`}
              onClick={() => onSeekEpisode(cue.start_ms)}
            />
          )
        })}
      </div>

      <div className="editor-trim-actions">
        <button
          type="button"
          className="btn ghost small"
          onClick={() => onTrimChange(snapStart(cues, episodeMs), trimEnd >= durationMs ? null : trimEnd)}
        >
          Start here
        </button>
        <button
          type="button"
          className="btn ghost small"
          onClick={() => onTrimChange(trimStart, snapEnd(cues, episodeMs))}
        >
          End here
        </button>
        <button
          type="button"
          className="btn text small"
          disabled={!trimmed}
          onClick={() => onTrimChange(0, null)}
        >
          Reset
        </button>
      </div>

      {scenes.length > 1 && (
        <div className="editor-clips">
          <span className="muted editor-clips-label">Clip one scene:</span>
          {scenes.map((scene, index) => (
            <button
              key={scene.scene_id}
              type="button"
              className="btn ghost small"
              onClick={() => onTrimChange(scene.start_ms, scene.end_ms)}
            >
              Scene {index + 1}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** The start of the line the playhead is in, so a trim never begins mid-sentence. */
function snapStart(cues: CaptionCue[], at: number): number {
  const cue = cues.find((item) => at >= item.start_ms && at < item.end_ms)
  return cue ? cue.start_ms : 0
}

/** The end of the line the playhead is in, so a trim never cuts a word off. */
function snapEnd(cues: CaptionCue[], at: number): number | null {
  const cue = cues.find((item) => at >= item.start_ms && at < item.end_ms)
  if (!cue) return null
  return cue.end_ms
}

function groupScenes(cues: CaptionCue[]): { scene_id: string; start_ms: number; end_ms: number }[] {
  const scenes: { scene_id: string; start_ms: number; end_ms: number }[] = []
  for (const cue of cues) {
    const last = scenes[scenes.length - 1]
    if (last && last.scene_id === cue.scene_id) {
      last.end_ms = cue.end_ms
    } else {
      scenes.push({ scene_id: cue.scene_id, start_ms: cue.start_ms, end_ms: cue.end_ms })
    }
  }
  return scenes
}

/** Word wrap, matching `video_edit.wrap_caption` so the preview breaks lines
 *  where the render will. */
function wrapText(text: string, width: number): string[] {
  if (!text) return []
  const lines: string[] = []
  let current: string[] = []
  let length = 0
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const needed = current.length === 0 ? word.length : word.length + 1
    if (current.length > 0 && length + needed > width) {
      lines.push(current.join(' '))
      current = [word]
      length = word.length
    } else {
      current.push(word)
      length += needed
    }
  }
  if (current.length > 0) lines.push(current.join(' '))
  return lines
}

export function clock(ms: number): string {
  const total = Math.max(Math.round(ms / 1000), 0)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}
