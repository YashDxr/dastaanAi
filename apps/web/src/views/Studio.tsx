import { useCallback, useEffect, useRef, useState } from 'react'
import { AppHeader } from '../components/AppHeader'
import { AudioPlayer } from '../components/AudioPlayer'
import { VideoPlayer, VideoPlayerEmpty } from '../components/VideoPlayer'
import { FeedbackComposer } from '../components/FeedbackComposer'
import { ProgressStepper } from '../components/ProgressStepper'
import { ScenePanel } from '../components/ScenePanel'
import { ScriptPanel } from '../components/ScriptPanel'
import { formatError, stories as storiesApi, watchProgress } from '../api'
import { applyEvent, emptyLive } from '../live'
import type { FeedbackEntry, Progress, StoryDetail, User } from '../types'

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
  // Live progress, folded from the event stream. Kept beside `progress` rather than
  // merged into it so the polled snapshot stays exactly what the server said.
  const [live, setLive] = useState(emptyLive)
  const [feedback, setFeedback] = useState<FeedbackEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<'episode' | 'scenes'>('episode')
  const [seekLineId, setSeekLineId] = useState<string | null>(null)
  // A respeak is only audible once assembly has rebuilt the mix, so the jump to
  // the line waits for that. These hold the request in the meantime: the line to
  // land on, and the version it was requested from, which is how a rebuilt mix is
  // told apart from the one being replaced.
  const respeakFromVersion = useRef<string | null>(null)
  const pendingRespeakLine = useRef<string | null>(null)
  // Assets from the last ready version — kept while regenerating so Play stays up.
  const [stickyAssets, setStickyAssets] = useState<StoryDetail['assets']>([])

  const refresh = useCallback(async () => {
    try {
      const [d, p, f] = await Promise.all([
        storiesApi.get(storyId),
        storiesApi.progress(storyId),
        storiesApi.feedbackHistory(storyId),
      ])
      setDetail(d)
      setProgress(p)
      setFeedback(f)
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
      // The rebuilt mix has landed, so the respeak is finally something the user
      // can hear. Batched with `setDetail` above so the player picks up the new
      // episode and the target line in one commit rather than seeking twice.
      const awaiting = pendingRespeakLine.current
      if (
        awaiting &&
        d.version?.id &&
        d.version.id !== respeakFromVersion.current &&
        d.assets.some((a) => a.kind === 'final_episode')
      ) {
        pendingRespeakLine.current = null
        respeakFromVersion.current = null
        setSeekLineId(awaiting)
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
    return watchProgress(storyId, {
      // `applyEvent` returns the same object when a frame changes nothing, so the
      // steady drip of heartbeats and token counts does not re-render the studio.
      onEvent: (event) => setLive((current) => applyEvent(current, event)),
      onRefresh: () => {
        void refresh()
      },
    })
  }, [storyId, progress?.status, detail?.story.status, refresh])

  // A new run starts from a clean overlay. Without this, the previews and counts
  // from the previous take would still be on screen while the next one warmed up.
  useEffect(() => {
    setLive(emptyLive)
  }, [storyId, detail?.version?.id])

  const state = detail?.state
  const regenerating = detail?.story.status === 'generating'
  // Interpretation happens before any job row exists, so the progress stepper
  // has nothing to show for it. This is what tells the user their note landed.
  const interpreting = feedback.some((f) => f.status === 'pending')
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
  const finalVideo =
    playerAssets.find((a) => a.kind === 'final_video') ??
    stickyAssets.find((a) => a.kind === 'final_video') ??
    null
  const wantsVideo =
    state?.output_format === 'video' || state?.output_format === 'both'
  const scenes = state?.scenes ?? []

  async function respeakLine(lineId: string) {
    setBusy(true)
    respeakFromVersion.current = detail?.version?.id ?? null
    // Note the line and leave playback alone. Seeking here would play the take
    // the user just asked to replace, and then the rebuilt mix would play the
    // line again - so a single respeak was heard twice. The disabled button and
    // the progress stepper already acknowledge the click.
    pendingRespeakLine.current = lineId
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
      pendingRespeakLine.current = null
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

            <ProgressStepper progress={progress} live={live} interpreting={interpreting} />

            <nav className="studio-tabs" role="tablist" aria-label="Studio sections">
              {(
                [
                  ['episode', 'Episode'],
                  ['scenes', 'Scenes'],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  id={`studio-tab-${id}`}
                  aria-selected={tab === id}
                  aria-controls={`studio-panel-${id}`}
                  className={tab === id ? 'active' : ''}
                  onClick={() => setTab(id)}
                >
                  {label}
                  {id === 'scenes' && scenes.length > 0 && (
                    <span className="tab-count">{scenes.length}</span>
                  )}
                </button>
              ))}
            </nav>

            {/* Both panels stay mounted and are hidden with CSS. Unmounting the
                Episode panel would tear down the audio element and stop playback
                the moment someone glanced at the scene art. */}
            <div
              className="studio-grid"
              id="studio-panel-episode"
              role="tabpanel"
              aria-labelledby="studio-tab-episode"
              hidden={tab !== 'episode'}
            >
              <div className="studio-main">
                <AudioPlayer
                  assets={playerAssets}
                  lines={state?.lines ?? []}
                  title={detail.story.title}
                  storyId={storyId}
                  seekLineId={seekLineId}
                  regenerating={regenerating}
                  onSeekHandled={() => setSeekLineId(null)}
                />
                {finalVideo ? (
                  <VideoPlayer
                    asset={finalVideo}
                    title={detail.story.title}
                    regenerating={regenerating}
                  />
                ) : wantsVideo ? (
                  <VideoPlayerEmpty regenerating={regenerating} />
                ) : null}
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
                <FeedbackComposer
                  disabled={busy || regenerating}
                  interpreting={interpreting}
                  history={feedback}
                  state={state}
                  onSubmit={async (text) => {
                    await storiesApi.feedback(storyId, text)
                    await refresh()
                  }}
                />
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
              </aside>
            </div>

            <div
              id="studio-panel-scenes"
              role="tabpanel"
              aria-labelledby="studio-tab-scenes"
              hidden={tab !== 'scenes'}
            >
              <ScenePanel
                scenes={scenes}
                images={images}
                lines={state?.lines ?? []}
                pending={regenerating || detail.story.status === 'generating'}
                onPlayScene={(lineId) => {
                  // Jump the player, then show it: the audio element lives in the
                  // Episode panel and is only hidden, so the seek still applies.
                  setSeekLineId(lineId)
                  setTab('episode')
                }}
              />
            </div>
          </>
        )}
      </main>
    </div>
  )
}
