import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AppHeader } from '../components/AppHeader'
import { AlternateEndings } from '../components/AlternateEndings'
import { AmbiencePanel } from '../components/AmbiencePanel'
import { AudioPlayer } from '../components/AudioPlayer'
import { CliffhangerPanel } from '../components/CliffhangerPanel'
import { VideoPlayer, VideoPlayerEmpty } from '../components/VideoPlayer'
import { DirectorControls } from '../components/DirectorControls'
import { FeedbackComposer } from '../components/FeedbackComposer'
import { ConsistencyPanel } from '../components/ConsistencyPanel'
import { ProgressStepper } from '../components/ProgressStepper'
import { ScenePanel } from '../components/ScenePanel'
import { ScriptPanel } from '../components/ScriptPanel'
import { CharacterAvatar } from '../components/CharacterAvatar'
import { VideoEditor } from '../components/editor/VideoEditor'
import { StoryTimeMachine } from '../components/StoryTimeMachine'
import { StoryGenomePanel } from '../components/StoryGenomePanel'
import { WritersRoomPanel } from '../components/WritersRoomPanel'
import { formatError, stories as storiesApi, watchProgress } from '../api'
import { applyEvent, emptyLive } from '../live'
import { studioLink } from '../routing'
import type { FeedbackEntry, Progress, StoryDetail, StudioTab, User, Version } from '../types'

type Props = {
  user: User
  storyId: string
  /** Which panel is open. Owned by `App` so it can come from a deep link and be
   *  written back to the URL, which is what makes the editor linkable. */
  tab: StudioTab
  onTab: (tab: StudioTab) => void
  onLogout: () => void
  onHome: () => void
  onCompose: () => void
}

export function Studio({ user, storyId, tab, onTab, onLogout, onHome, onCompose }: Props) {
  const [detail, setDetail] = useState<StoryDetail | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  // Live progress, folded from the event stream. Kept beside `progress` rather than
  // merged into it so the polled snapshot stays exactly what the server said.
  const [live, setLive] = useState(emptyLive)
  const [feedback, setFeedback] = useState<FeedbackEntry[]>([])
  const [versions, setVersions] = useState<Version[]>([])
  const [baseVersionId, setBaseVersionId] = useState<string | null>(null)
  const [baseDetail, setBaseDetail] = useState<StoryDetail | null>(null)
  const [loadingBaseVersion, setLoadingBaseVersion] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [seekLineId, setSeekLineId] = useState<string | null>(null)
  const [activeLineId, setActiveLineId] = useState<string | null>(null)
  // The editor loads a timeline, artwork and every saved cut, so it is mounted on
  // first visit rather than with the other panels — and then kept mounted, so
  // stepping back to the episode does not throw away an in-progress edit.
  const [editorMounted, setEditorMounted] = useState(tab === 'editor')
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

  useEffect(() => {
    if (tab === 'editor') setEditorMounted(true)
  }, [tab])

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
  // Character avatar portraits keyed by character_id (stored in asset.line_id).
  const avatarUrls = useMemo(() => {
    const map = new Map<string, string>()
    for (const a of detail?.assets ?? []) {
      if (a.kind === 'character_avatar' && a.line_id) map.set(a.line_id, a.url)
    }
    return map
  }, [detail?.assets])
  const hasMusicBed = playerAssets.some((a) => a.kind === 'music_bed')
    || stickyAssets.some((a) => a.kind === 'music_bed')
  // The editor cuts scene artwork against the recorded lines, so it is only worth
  // pointing at once both exist and the run has finished producing them.
  const canEdit =
    detail?.story.status === 'ready' && images.length > 0 && (state?.lines.length ?? 0) > 0
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

            <ProgressStepper progress={progress} live={live} interpreting={interpreting} />

            <nav className="studio-tabs" role="tablist" aria-label="Studio sections">
              {(
                [
                  ['episode', 'Episode'],
                  ['scenes', 'Scenes'],
                  ['revisions', 'Revisions'],
                  ['editor', 'Editor'],
                  ['writers-room', 'Writers Room'],
                  ['cliffhanger', 'Cliffhanger'],
                  ['genome', 'Story DNA'],
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
                  onClick={() => onTab(id)}
                >
                  {label}
                  {id === 'scenes' && scenes.length > 0 && (
                    <span className="tab-count">{scenes.length}</span>
                  )}
                  {id === 'editor' && canEdit && <span className="tab-count">New</span>}
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
                  hasMusicBed={hasMusicBed}
                  onSeekHandled={() => setSeekLineId(null)}
                  onActiveLineChange={setActiveLineId}
                />
                {finalVideo ? (
                  <>
                    <VideoPlayer
                      asset={finalVideo}
                      title={detail.story.title}
                      regenerating={regenerating}
                    />
                    <EditorInvite storyId={storyId} onOpen={() => onTab('editor')} />
                  </>
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
                  activeLineId={activeLineId}
                  onRegenerateLine={respeakLine}
                  onSeekLine={setSeekLineId}
                  avatarUrls={avatarUrls}
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
                <DirectorControls
                  storyId={storyId}
                  state={state ?? null}
                  busy={busy}
                  regenerating={regenerating}
                  onRegenerated={refresh}
                />
              </div>

              <aside className="studio-side">
                <section className="cast-panel">
                  <p className="eyebrow">Cast</p>
                  <ul>
                    {(state?.characters ?? []).map((c) => (
                      <li key={c.id} className="cast-item">
                        <CharacterAvatar name={c.name} role={c.role} size="md" imageUrl={avatarUrls.get(c.id)} />
                        <div>
                          <strong>{c.name}</strong>
                          <span className="muted">{c.role.replaceAll('_', ' ')}</span>
                          <p>{c.personality}</p>
                        </div>
                      </li>
                    ))}
                    {!state?.characters?.length && <li className="muted">Casting in progress…</li>}
                  </ul>
                </section>
              </aside>
            </div>

            <div
              id="studio-panel-editor"
              role="tabpanel"
              aria-labelledby="studio-tab-editor"
              hidden={tab !== 'editor'}
            >
              {editorMounted && <VideoEditor storyId={storyId} active={tab === 'editor'} />}
            </div>

            <div
              className="scenes-tab"
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
                  setSeekLineId(lineId)
                  onTab('episode')
                }}
              />
              {scenes.length > 0 && (
                <AmbiencePanel
                  scenes={scenes}
                  lines={state?.lines ?? []}
                  hasMusicBed={hasMusicBed}
                />
              )}
            </div>

            <div
              id="studio-panel-revisions"
              role="tabpanel"
              aria-labelledby="studio-tab-revisions"
              hidden={tab !== 'revisions'}
            >
              <div className="revisions-panel">
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
                {detail.story.status === 'ready' &&
                  state &&
                  state.scenes.length > 0 &&
                  state.lines.length > 0 && (
                    <ConsistencyPanel
                      storyId={storyId}
                      state={state}
                      disabled={busy || regenerating}
                    />
                  )}
              </div>
            </div>

            <div
              id="studio-panel-writers-room"
              role="tabpanel"
              aria-labelledby="studio-tab-writers-room"
              hidden={tab !== 'writers-room'}
            >
              <WritersRoomPanel
                storyId={storyId}
                ready={detail.story.status === 'ready'}
              />
            </div>

            <div
              id="studio-panel-cliffhanger"
              role="tabpanel"
              aria-labelledby="studio-tab-cliffhanger"
              hidden={tab !== 'cliffhanger'}
            >
              <CliffhangerPanel
                storyId={storyId}
                ready={detail.story.status === 'ready'}
              />
            </div>

            <div
              id="studio-panel-genome"
              role="tabpanel"
              aria-labelledby="studio-tab-genome"
              hidden={tab !== 'genome'}
            >
              <StoryGenomePanel
                storyId={storyId}
                ready={detail.story.status === 'ready'}
              />
            </div>
          </>
        )}
      </main>
    </div>
  )
}

/**
 * The handoff from "your video is ready" to the editor.
 *
 * A button and a link, both: the button is the obvious next step for someone
 * already on the page, and the anchor carries the deep link, so it can be
 * middle-clicked, copied, or pasted into the message that tells someone their
 * episode is done.
 */
function EditorInvite({ storyId, onOpen }: { storyId: string; onOpen: () => void }) {
  return (
    <section className="editor-invite">
      <div>
        <p className="eyebrow">Make it shareable</p>
        <p className="muted">
          Trim it, restyle the captions, cut a vertical version for Reels, lay your own
          music underneath, and get a link anyone can watch.
        </p>
      </div>
      <a
        className="btn primary"
        href={studioLink(storyId, 'editor')}
        onClick={(event) => {
          // Same-document navigation, so the click is handled in place rather than
          // letting the fragment change and the app re-derive the view from it.
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
          event.preventDefault()
          onOpen()
        }}
      >
        Open the editor
      </a>
    </section>
  )
}
