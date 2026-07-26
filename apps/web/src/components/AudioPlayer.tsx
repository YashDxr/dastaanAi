import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BgmPanel } from './BgmPanel'
import { ExportPanel } from './ExportPanel'
import { VideoExportPanel } from './VideoExportPanel'
import type { Asset, DialogueLine } from '../types'

const DOWNLOAD_ICON = (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
    <path d="M7 1v8M4 7l3 3 3-3M2 12h10" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
)

type LineSpan = { lineId: string; startSec: number; endSec: number }

/** Precompute the [start, end) time span for every line in the final mix. */
function buildTimeline(assets: Asset[], lines: DialogueLine[]): LineSpan[] {
  const byLine = new Map(
    assets
      .filter((a) => a.kind === 'line_audio' && a.line_id)
      .map((a) => [a.line_id as string, a]),
  )
  const sorted = [...lines].sort((a, b) => a.index - b.index)
  const spans: LineSpan[] = []
  let cursor = 0
  for (const line of sorted) {
    const clip = byLine.get(line.id)
    const dur = (clip?.duration_ms ?? 0) / 1000
    spans.push({ lineId: line.id, startSec: cursor, endSec: cursor + dur })
    cursor += dur + (line.pause_after_ms ?? 0) / 1000
  }
  return spans
}

/** Find which line is playing at the given time. */
function activeLineAt(timeline: LineSpan[], timeSec: number): string | null {
  // Linear scan is fine for typical story sizes (< 200 lines)
  for (let i = timeline.length - 1; i >= 0; i--) {
    if (timeSec >= timeline[i].startSec) return timeline[i].lineId
  }
  return timeline[0]?.lineId ?? null
}

type Props = {
  assets: Asset[]
  lines?: DialogueLine[]
  title?: string | null
  /** After respeak / regen, move the playhead to this line once a mix is available. */
  seekLineId?: string | null
  onSeekHandled?: () => void
  /** Fires when the currently-playing line changes during playback. */
  onActiveLineChange?: (lineId: string | null) => void
  /** Story is regenerating — keep showing the previous episode instead of empty. */
  regenerating?: boolean
  /** Enables the download panels. Omitted while a mix does not exist yet. */
  storyId?: string
  /** Show the BGM download option when a music bed has been generated. */
  hasMusicBed?: boolean
  /** Show the video download option once the pipeline has rendered one. */
  hasVideo?: boolean
}

function formatTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

/** Approximate start time of a line in the final mix from per-line durations + pauses. */
export function lineSeekSeconds(
  assets: Asset[],
  lines: DialogueLine[],
  lineId: string,
): number {
  const byLine = new Map(
    assets
      .filter((a) => a.kind === 'line_audio' && a.line_id)
      .map((a) => [a.line_id as string, a]),
  )
  let seconds = 0
  for (const line of [...lines].sort((a, b) => a.index - b.index)) {
    if (line.id === lineId) return seconds
    const clip = byLine.get(line.id)
    seconds += (clip?.duration_ms ?? 0) / 1000
    seconds += (line.pause_after_ms ?? 0) / 1000
  }
  return seconds
}

export function AudioPlayer({
  assets,
  lines = [],
  title,
  seekLineId,
  onSeekHandled,
  onActiveLineChange,
  regenerating = false,
  storyId,
  hasMusicBed = false,
  hasVideo = false,
}: Props) {
  const liveEpisode = useMemo(
    () => assets.find((a) => a.kind === 'final_episode') ?? null,
    [assets],
  )

  // Keep the last playable episode across regenerations so Play never vanishes
  // while assembly rebuilds a new mix.
  const stickyEpisode = useRef<Asset | null>(null)
  if (liveEpisode) stickyEpisode.current = liveEpisode
  const episode = liveEpisode ?? stickyEpisode.current
  const episodeId = episode?.id ?? null
  const showingPrevious = regenerating && !liveEpisode && !!episode

  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)
  // Non-null only while the user is dragging the scrubber. Holding the thumb
  // position here stops `timeupdate` from yanking it back under the cursor.
  const [scrub, setScrub] = useState<number | null>(null)

  const playingRef = useRef(false)
  const currentRef = useRef(0)
  const scrubRef = useRef<number | null>(null)
  scrubRef.current = scrub

  // A seek asked for before the element has metadata is replayed on load.
  const pendingSeek = useRef<number | null>(null)
  const resumeAfterSeek = useRef(false)

  // Precomputed timeline for active-line tracking. Rebuilt when assets or lines
  // change (which only happens on poll, not on every frame).
  const timeline = useMemo(() => buildTimeline(assets, lines), [assets, lines])
  const timelineRef = useRef(timeline)
  timelineRef.current = timeline
  const activeLineRef = useRef<string | null>(null)
  const onActiveLineChangeRef = useRef(onActiveLineChange)
  onActiveLineChangeRef.current = onActiveLineChange

  // Both arrays are rebuilt on every poll. The seek effect reads them through a
  // ref so it can key off ids alone — depending on the arrays would re-run it on
  // every render and repeatedly drag the playhead back to the line start.
  const latest = useRef({ assets, lines, onSeekHandled })
  latest.current = { assets, lines, onSeekHandled }

  const applyPendingSeek = useCallback(() => {
    const el = audioRef.current
    if (!el || pendingSeek.current === null) return
    const target = Number.isFinite(el.duration)
      ? Math.min(pendingSeek.current, Math.max(el.duration - 0.05, 0))
      : pendingSeek.current
    pendingSeek.current = null
    el.currentTime = target
    currentRef.current = target
    setCurrent(target)
    if (resumeAfterSeek.current) {
      resumeAfterSeek.current = false
      void el.play().catch(() => undefined)
    }
    latest.current.onSeekHandled?.()
  }, [])

  // Swapping in a rebuilt mix. Carry the listener's position and play state over
  // instead of silently dropping them back to zero.
  const loadedEpisodeId = useRef<string | null>(null)
  useEffect(() => {
    if (loadedEpisodeId.current === episodeId) return
    const isSwap = loadedEpisodeId.current !== null
    loadedEpisodeId.current = episodeId
    setCurrent(0)
    setDuration(0)
    setScrub(null)
    if (!episodeId) return
    if (isSwap) {
      pendingSeek.current = currentRef.current
      resumeAfterSeek.current = playingRef.current
    }
    // Assigning src alone does not reliably restart loading in every browser.
    audioRef.current?.load()
  }, [episodeId])

  useEffect(() => {
    if (!seekLineId || !episodeId) return
    const { assets: a, lines: l } = latest.current
    pendingSeek.current = lineSeekSeconds(a, l, seekLineId)
    // Respeaking is an edit, not a play command: only resume if already playing.
    resumeAfterSeek.current = playingRef.current
    if ((audioRef.current?.readyState ?? 0) >= 1) applyPendingSeek()
  }, [seekLineId, episodeId, applyPendingSeek])

  const commitScrub = useCallback(() => {
    const value = scrubRef.current
    setScrub(null)
    if (value === null) return
    const el = audioRef.current
    if (el) el.currentTime = value
    currentRef.current = value
    setCurrent(value)
  }, [])

  // The pointer is regularly released off the thumb, and sometimes outside the
  // window, so the drag has to be ended globally rather than on the input.
  const isDragging = scrub !== null
  useEffect(() => {
    if (!isDragging) return
    const finish = () => commitScrub()
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
    return () => {
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
    }
  }, [isDragging, commitScrub])

  if (!episode) {
    return (
      <section className="player empty">
        <p className="eyebrow">Episode</p>
        <h2>{regenerating ? 'Rebuilding the mix…' : 'Waiting for the final mix'}</h2>
        <p className="muted">
          {regenerating
            ? 'Speech and assembly are running. Playback will resume when ready.'
            : 'Audio appears here once assembly finishes.'}
        </p>
      </section>
    )
  }

  const displayTime = scrub ?? current
  const seekable = duration > 0

  return (
    <section className="player">
      <p className="eyebrow">
        {showingPrevious ? 'Previous mix (rebuild in progress)' : 'Now playing'}
      </p>
      <h2>{title || 'Untitled episode'}</h2>
      <audio
        ref={audioRef}
        src={episode.url}
        preload="metadata"
        onTimeUpdate={() => {
          const time = audioRef.current?.currentTime ?? 0
          currentRef.current = time
          if (scrubRef.current === null) setCurrent(time)
          // Active-line tracking: only fire callback when the line changes
          const nowLine = activeLineAt(timelineRef.current, time)
          if (nowLine !== activeLineRef.current) {
            activeLineRef.current = nowLine
            onActiveLineChangeRef.current?.(nowLine)
          }
        }}
        onDurationChange={() => {
          const value = audioRef.current?.duration
          setDuration(Number.isFinite(value) ? (value as number) : 0)
        }}
        onLoadedMetadata={() => {
          const value = audioRef.current?.duration
          setDuration(Number.isFinite(value) ? (value as number) : 0)
          // Loading a source resets the rate, so a rebuilt mix would silently
          // drop back to 1x mid-episode without this.
          if (audioRef.current) audioRef.current.playbackRate = playbackRate
          applyPendingSeek()
        }}
        onEnded={() => {
          playingRef.current = false
          setPlaying(false)
          activeLineRef.current = null
          onActiveLineChangeRef.current?.(null)
        }}
        onPlay={() => {
          playingRef.current = true
          setPlaying(true)
          if (audioRef.current) audioRef.current.playbackRate = playbackRate
        }}
        onPause={() => {
          playingRef.current = false
          setPlaying(false)
        }}
      />
      <div className="player-controls">
        <button
          type="button"
          className="play-btn"
          aria-label={playing ? 'Pause' : 'Play'}
          onClick={() => {
            const el = audioRef.current
            if (!el) return
            if (el.paused) void el.play().catch(() => undefined)
            else el.pause()
          }}
        >
          {playing ? 'Pause' : 'Play'}
        </button>
        <div className="speed-controls" role="group" aria-label="Playback speed">
          {[0.75, 1, 1.25, 1.5, 2].map((rate) => (
            <button
              key={rate}
              type="button"
              className={`speed-btn${playbackRate === rate ? ' active' : ''}`}
              aria-pressed={playbackRate === rate}
              onClick={() => {
                setPlaybackRate(rate)
                if (audioRef.current) audioRef.current.playbackRate = rate
              }}
            >
              {rate}x
            </button>
          ))}
        </div>
        <div className="scrubber">
          <input
            type="range"
            min={0}
            max={seekable ? duration : 0}
            step={0.05}
            value={displayTime}
            disabled={!seekable}
            aria-label="Seek"
            aria-valuetext={formatTime(displayTime)}
            onPointerDown={() => {
              scrubRef.current = currentRef.current
              setScrub(currentRef.current)
            }}
            onChange={(e) => {
              const value = Number(e.target.value)
              const dragging = scrubRef.current !== null
              scrubRef.current = value
              setScrub(value)
              // Keyboard input produces no drag to wait for, so commit at once.
              if (!dragging) commitScrub()
            }}
            onKeyUp={() => commitScrub()}
            onBlur={() => commitScrub()}
          />
          <div className="time-row">
            <span>{formatTime(displayTime)}</span>
            <span>{formatTime(duration)}</span>
          </div>
        </div>
      </div>
      {storyId && (
        <DownloadsSection
          storyId={storyId}
          episodeReady={!!liveEpisode}
          hasMusicBed={hasMusicBed}
          hasVideo={hasVideo}
        />
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// Unified Downloads section — one toggle, clearly-labelled sub-groups inside.
// ---------------------------------------------------------------------------

function DownloadsSection({
  storyId,
  episodeReady,
  hasMusicBed,
  hasVideo,
}: {
  storyId: string
  episodeReady: boolean
  hasMusicBed: boolean
  hasVideo: boolean
}) {
  const [open, setOpen] = useState(false)

  if (!episodeReady && !hasMusicBed && !hasVideo) return null

  return (
    <div className="downloads-section">
      <button
        type="button"
        className="downloads-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="downloads-toggle-label">
          {DOWNLOAD_ICON}
          Downloads
        </span>
        <span className="downloads-toggle-caret" aria-hidden>{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <div className="downloads-body">
          {episodeReady && (
            <div className="download-group">
              <p className="download-group-heading">Episode Audio</p>
              <ExportPanel storyId={storyId} enabled inline />
            </div>
          )}

          {hasMusicBed && (
            <div className="download-group">
              <p className="download-group-heading">Background Score</p>
              <BgmPanel storyId={storyId} inline />
            </div>
          )}

          {hasVideo && (
            <div className="download-group">
              <p className="download-group-heading">Episode Video</p>
              <VideoExportPanel storyId={storyId} inline />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
