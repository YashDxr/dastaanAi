import { useCallback, useEffect, useRef, useState } from 'react'
import { AppHeader } from '../components/AppHeader'
import { AlternateEndings } from '../components/AlternateEndings'
import { AudioPlayer } from '../components/AudioPlayer'
import { FeedbackComposer } from '../components/FeedbackComposer'
import { ConsistencyPanel } from '../components/ConsistencyPanel'
import { ProgressStepper } from '../components/ProgressStepper'
import { ScenePanel } from '../components/ScenePanel'
import { ScriptPanel } from '../components/ScriptPanel'
import { StoryTimeMachine } from '../components/StoryTimeMachine'
import { formatError, stories as storiesApi, watchProgress } from '../api'
import type { FeedbackEntry, Progress, StoryDetail, User, Version } from '../types'

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
  const [feedback, setFeedback] = useState<FeedbackEntry[]>([])
  const [versions, setVersions] = useState<Version[]>([])
  const [baseVersionId, setBaseVersionId] = useState<string | null>(null)
  const [baseDetail, setBaseDetail] = useState<StoryDetail | null>(null)
  const [loadingBaseVersion, setLoadingBaseVersion] = useState(false)
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
  const baseVersionRequest = useRef(0)
  // Assets from the last ready version — kept while regenerating so Play stays up.
  const [stickyAssets, setStickyAssets] = useState<StoryDetail['assets']>([])

  const refresh = useCallback(async () => {
    try {
      const [d, p, f, v] = await Promise.all([
        storiesApi.get(storyId),
        storiesApi.progress(storyId),
        storiesApi.feedbackHistory(storyId),
        storiesApi.versions(storyId),
      ])
      setDetail(d)
      setProgress(p)
      setFeedback(f)
      setVersions(v)
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
    // On first open, the current revision is the branch source. Once the
    // listener intentionally picks a historic version we leave that choice in
    // place while progress refreshes the current child in the background.
    const currentVersionId = detail?.version?.id
    if (currentVersionId) setBaseVersionId((selected) => selected ?? currentVersionId)
  }, [detail?.version?.id])

  useEffect(() => {
    const status = progress?.status ?? detail?.story.status
    if (status !== 'generating') return
    return watchProgress(storyId, () => {
      void refresh()
    })
  }, [storyId, progress?.status, detail?.story.status, refresh])

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
  const scenes = state?.scenes ?? []
  const timelineDetail =
    baseVersionId && baseVersionId !== detail?.version?.id ? baseDetail : detail
  const timelineState = timelineDetail?.state

  async function selectBaseVersion(versionId: string) {
    const request = ++baseVersionRequest.current
    setBaseVersionId(versionId)
    if (versionId === detail?.version?.id) {
      setBaseDetail(null)
      setLoadingBaseVersion(false)
      return
    }

    setLoadingBaseVersion(true)
    try {
      const selected = await storiesApi.version(storyId, versionId)
      if (request === baseVersionRequest.current) {
        setBaseDetail(selected)
        setError(null)
      }
    } catch (err) {
      if (request === baseVersionRequest.current) {
        setBaseVersionId(detail?.version?.id ?? null)
        setBaseDetail(null)
        setError(formatError(err))
      }
    } finally {
      if (request === baseVersionRequest.current) setLoadingBaseVersion(false)
    }
  }

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

  async function branchFromScene(
    sceneId: string,
    instructionDelta: string,
    sourceVersionId: string,
  ) {
    setBusy(true)
    try {
      const accepted = await storiesApi.regenerate(storyId, {
        scope: 'scene',
        target_stage: 'story_understanding',
        target_id: sceneId,
        instruction_delta: instructionDelta,
        base_version_id: sourceVersionId,
        expected_current_version_id: detail?.version?.id ?? null,
      })
      // The new child becomes current, so show its in-flight timeline rather
      // than leaving the Time Machine focused on the historic branch source.
      setBaseVersionId(accepted.version_id)
      setBaseDetail(null)
      await refresh()
    } catch (err) {
      setError(formatError(err))
      throw err
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

            <ProgressStepper progress={progress} interpreting={interpreting} />

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
                <StoryTimeMachine
                  scenes={timelineState?.scenes ?? []}
                  lines={timelineState?.lines ?? []}
                  versions={versions}
                  currentVersionId={detail.version?.id}
                  baseVersionId={baseVersionId}
                  loadingVersion={loadingBaseVersion}
                  disabled={busy || regenerating || detail.story.status !== 'ready' || !detail.version}
                  onSelectBaseVersion={selectBaseVersion}
                  onCreateBranch={branchFromScene}
                />
                {detail.story.status === 'ready' && (
                  <AlternateEndings
                    storyId={storyId}
                    state={state}
                    disabled={busy || regenerating}
                    onRequested={refresh}
                  />
                )}
                {detail.story.status === 'ready' && state && state.scenes.length > 0 && state.lines.length > 0 && (
                  <ConsistencyPanel
                    storyId={storyId}
                    state={state}
                    disabled={busy || regenerating}
                  />
                )}
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
