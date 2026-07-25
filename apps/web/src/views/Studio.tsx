import { useCallback, useEffect, useRef, useState } from 'react'
import { AppHeader } from '../components/AppHeader'
import { AudioPlayer } from '../components/AudioPlayer'
import { FeedbackComposer } from '../components/FeedbackComposer'
import { ProgressStepper } from '../components/ProgressStepper'
import { ScriptPanel } from '../components/ScriptPanel'
import { formatError, stories as storiesApi, watchProgress } from '../api'
import type { Progress, StoryDetail, User } from '../types'

type Props = {
  user: User
  storyId: string
  onLogout: () => void
  onHome: () => void
  onCompose: () => void
}

export function Studio({ user, storyId, onLogout, onHome, onCompose }: Props) {
  const [detail, setDetail] = useState<StoryDetail | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [seekLineId, setSeekLineId] = useState<string | null>(null)
  // The version a respeak was requested from. The seek has to survive until the
  // rebuilt mix arrives, so it can land on the same line in the new episode and
  // not just in the one being replaced.
  const respeakFromVersion = useRef<string | null>(null)
  // Assets from the last ready version — kept while regenerating so Play stays up.
  const [stickyAssets, setStickyAssets] = useState<StoryDetail['assets']>([])

  const refresh = useCallback(async () => {
    try {
      const [d, p] = await Promise.all([
        storiesApi.get(storyId),
        storiesApi.progress(storyId),
      ])
      setDetail(d)
      setProgress(p)
      setError(null)
      if (d.assets.some((a) => a.kind === 'final_episode')) {
        setStickyAssets(d.assets)
      } else if (d.assets.length) {
        // Merge the in-flight version's clips in so seek math stays accurate
        // during a regen. Keyed by what the asset depicts rather than by id: a
        // fork mints new rows for carried-over artifacts, and matching on id
        // would stack a duplicate set on every revision.
        setStickyAssets((prev) => {
          const slot = (a: StoryDetail['assets'][number]) =>
            `${a.kind}:${a.line_id ?? a.scene_id ?? 'single'}`
          const merged = new Map(prev.map((a) => [slot(a), a]))
          for (const asset of d.assets) merged.set(slot(asset), asset)
          return [...merged.values()]
        })
      }
    } catch (err) {
      setError(formatError(err))
    }
  }, [storyId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const status = progress?.status ?? detail?.story.status
    if (status !== 'generating') return
    return watchProgress(storyId, () => {
      void refresh()
    })
  }, [storyId, progress?.status, detail?.story.status, refresh])

  const state = detail?.state
  const regenerating = detail?.story.status === 'generating'
  // A regeneration forks a new version and carries reusable assets over, but the
  // final mix is always rebuilt. Fall back to the last ready set so the player and
  // the scene art stay on screen through that gap instead of blinking out.
  const playerAssets =
    detail?.assets.some((a) => a.kind === 'final_episode')
      ? detail.assets
      : stickyAssets.length
        ? stickyAssets
        : (detail?.assets ?? [])
  const liveImages = detail?.assets.filter((a) => a.kind === 'scene_image') ?? []
  const images = liveImages.length
    ? liveImages
    : stickyAssets.filter((a) => a.kind === 'scene_image')

  async function respeakLine(lineId: string) {
    setBusy(true)
    respeakFromVersion.current = detail?.version?.id ?? null
    // Move the current mix to this line right away so the click feels local,
    // then kick off the regeneration that rebuilds it.
    setSeekLineId(lineId)
    try {
      await storiesApi.regenerate(storyId, {
        scope: 'line',
        target_stage: 'tts_synthesis',
        target_id: lineId,
        instruction_delta: 'Deliver this line with clearer emotional intent.',
      })
      await refresh()
    } catch (err) {
      setError(formatError(err))
      respeakFromVersion.current = null
      setSeekLineId(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="app-frame">
      <AppHeader
        email={user.email}
        onLogout={onLogout}
        onHome={onHome}
        onCompose={onCompose}
        compact
      />
      <main className="studio">
        {!detail ? (
          <p className="muted loading-copy">Opening studio…</p>
        ) : (
          <>
            <header className="studio-head">
              <div>
                <p className="eyebrow">Studio</p>
                <h1>{detail.story.title ?? 'Untitled story'}</h1>
                <p className="muted">
                  {[detail.version?.genre, detail.version?.mood].filter(Boolean).join(' · ') ||
                    'Episode in progress'}
                </p>
              </div>
              <button type="button" className="btn ghost" onClick={onHome}>
                Back to library
              </button>
            </header>

            {error && <p className="form-error">{error}</p>}

            <ProgressStepper progress={progress} />

            <div className="studio-grid">
              <div className="studio-main">
                <AudioPlayer
                  assets={playerAssets}
                  lines={state?.lines ?? []}
                  title={detail.story.title}
                  seekLineId={seekLineId}
                  regenerating={regenerating}
                  onSeekHandled={() => {
                    // Seeking the outgoing mix is only a courtesy jump. Keep the
                    // request alive until a newer version has been seeked too.
                    const from = respeakFromVersion.current
                    if (from && detail?.version?.id === from) return
                    respeakFromVersion.current = null
                    setSeekLineId(null)
                  }}
                />
                {state?.arc_summary && (
                  <section className="arc-panel">
                    <p className="eyebrow">Arc</p>
                    <p>{state.arc_summary}</p>
                  </section>
                )}
                <ScriptPanel
                  lines={state?.lines ?? []}
                  characters={state?.characters ?? []}
                  scenes={state?.scenes ?? []}
                  busy={busy || regenerating}
                  onRegenerateLine={respeakLine}
                />
                {detail.story.status === 'ready' && (
                  <FeedbackComposer
                    disabled={busy}
                    onSubmit={async (text) => {
                      await storiesApi.feedback(storyId, text)
                      await refresh()
                    }}
                  />
                )}
              </div>

              <aside className="studio-side">
                <section className="cast-panel">
                  <p className="eyebrow">Cast</p>
                  <ul>
                    {(state?.characters ?? []).map((c) => (
                      <li key={c.id}>
                        <strong>{c.name}</strong>
                        <span className="muted">{c.role.replaceAll('_', ' ')}</span>
                        <p>{c.personality}</p>
                      </li>
                    ))}
                    {!state?.characters?.length && <li className="muted">Casting in progress…</li>}
                  </ul>
                </section>

                <section className="scenes-panel">
                  <p className="eyebrow">Scenes</p>
                  <ul>
                    {(state?.scenes ?? []).map((scene) => {
                      const image = images.find((a) => a.scene_id === scene.id)
                      return (
                        <li key={scene.id}>
                          {image && (
                            <img src={image.url} alt="" className="scene-thumb" loading="lazy" />
                          )}
                          <strong>{scene.title}</strong>
                          <p className="muted">{scene.summary}</p>
                        </li>
                      )
                    })}
                    {!state?.scenes?.length && <li className="muted">Blocking scenes…</li>}
                  </ul>
                </section>
              </aside>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
