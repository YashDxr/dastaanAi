import { apiFetch } from '@daastaan/api-types'
import { useCallback, useEffect, useMemo, useState } from 'react'

type ReviewStatus = 'pending' | 'approved' | 'flagged' | 'changes_requested'
type ReviewAction = 'approve' | 'flag' | 'changes_requested' | 'clear_flag'

type AssetCoverage = {
  kind: string
  expected: number
  complete: number
  missing: number
  placeholders: number
  required: boolean
}

type QualityReport = {
  version_id: string | null
  state_available: boolean
  ready_for_review: boolean
  required_missing: number
  placeholder_count: number
  coverage: AssetCoverage[]
  warnings: string[]
}

type StoryReview = {
  status: ReviewStatus
  note: string | null
  reviewed_by: string | null
  reviewed_at: string | null
}

type AdminStory = {
  id: string
  title: string | null
  status: string
  current_version_id: string | null
  flagged: boolean
  created_at: string
  owner: { id: string; email: string } | null
  review: StoryReview
  quality: QualityReport
}

type AdminAsset = {
  id: string
  kind: string
  line_id: string | null
  scene_id: string | null
  content_type: string
  duration_ms: number | null
  url: string
}

type AuditEntry = {
  id: string
  actor_user_id: string | null
  action: string
  target_type: string | null
  target_id: string | null
  metadata: Record<string, unknown> | null
  created_at: string
}

type AdminStoryDetail = AdminStory & {
  version: { id: string; version_number: number; genre: string | null; mood: string | null } | null
  state: Record<string, unknown> | null
  assets: AdminAsset[]
  review_history: AuditEntry[]
}

type StoriesTabProps = {
  onError: (message: string | null) => void
}

const REVIEW_LABELS: Record<ReviewStatus, string> = {
  pending: 'Pending review',
  approved: 'Approved',
  flagged: 'Flagged',
  changes_requested: 'Changes requested',
}

const REVIEW_ACTIONS: { value: ReviewAction; label: string; requiresNote?: boolean }[] = [
  { value: 'approve', label: 'Approve for release' },
  { value: 'changes_requested', label: 'Request changes', requiresNote: true },
  { value: 'flag', label: 'Flag and hold', requiresNote: true },
  { value: 'clear_flag', label: 'Clear flag' },
]

function pretty(value: string) {
  return value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
}

function qualityCopy(quality: QualityReport) {
  if (!quality.state_available) return 'Awaiting an analyzable story version'
  if (quality.ready_for_review) return 'Required assets are complete'
  return `${quality.required_missing} required asset${quality.required_missing === 1 ? '' : 's'} missing`
}

function getArray(value: unknown) {
  return Array.isArray(value) ? value : []
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function AssetPreview({ asset }: { asset: AdminAsset }) {
  if (asset.kind === 'final_episode' || asset.kind === 'line_audio' || asset.kind === 'music_bed') {
    return <audio controls preload="metadata" src={asset.url} className="review-media" />
  }
  if (asset.kind === 'final_video') {
    return <video controls preload="metadata" src={asset.url} className="review-media review-video" />
  }
  if (asset.kind === 'scene_image') {
    return <img src={asset.url} alt="Generated scene artwork" className="review-image" loading="lazy" />
  }
  return null
}

function ReviewBadge({ status }: { status: ReviewStatus }) {
  return <span className={`badge review-${status}`}>{REVIEW_LABELS[status]}</span>
}

function StoryDetailPanel({
  detail,
  saving,
  onBack,
  onReview,
}: {
  detail: AdminStoryDetail
  saving: boolean
  onBack: () => void
  onReview: (action: ReviewAction, note: string) => void
}) {
  const [action, setAction] = useState<ReviewAction>('approve')
  const [note, setNote] = useState('')
  const availableActions = REVIEW_ACTIONS.filter(
    (item) => item.value !== 'clear_flag' || detail.flagged,
  )
  const selectedAction = availableActions.find((item) => item.value === action)
  const requiresNote = selectedAction?.requiresNote ?? false
  const approveBlocked =
    action === 'approve' &&
    (detail.flagged || detail.status !== 'ready' || !detail.quality.ready_for_review)
  const assetsByKind = useMemo(() => {
    const grouped: Record<string, AdminAsset[]> = {}
    for (const asset of detail.assets) {
      const assets = grouped[asset.kind] ?? []
      assets.push(asset)
      grouped[asset.kind] = assets
    }
    return grouped
  }, [detail.assets])
  const lines = getArray(detail.state?.lines).map(asRecord).filter((line): line is Record<string, unknown> => Boolean(line))
  const scenes = getArray(detail.state?.scenes).map(asRecord).filter((scene): scene is Record<string, unknown> => Boolean(scene))

  useEffect(() => {
    if (!availableActions.some((item) => item.value === action)) setAction('approve')
  }, [action, availableActions])

  return (
    <>
      <section className="panel review-detail-head">
        <button type="button" className="btn ghost small" onClick={onBack}>
          ← Review queue
        </button>
        <div className="review-detail-title">
          <div>
            <p className="eyebrow">Content review</p>
            <h2>{detail.title ?? 'Untitled story'}</h2>
            <p className="muted">
              {detail.owner?.email ?? 'Unknown owner'} · {new Date(detail.created_at).toLocaleString()}
              {detail.version ? ` · v${detail.version.version_number}` : ''}
            </p>
          </div>
          <div className="review-badges">
            <ReviewBadge status={detail.review.status} />
            <span className={`badge ${detail.status}`}>{pretty(detail.status)}</span>
          </div>
        </div>
      </section>

      <section className={`panel quality-panel ${detail.quality.ready_for_review ? 'quality-ready' : 'quality-warning'}`}>
        <div className="panel-head">
          <div>
            <h2>Release quality</h2>
            <p className="muted">{qualityCopy(detail.quality)}. This checks the current version only.</p>
          </div>
          <span className={`quality-status ${detail.quality.ready_for_review ? 'ready' : 'blocked'}`}>
            {detail.quality.ready_for_review ? 'Ready for review' : 'Needs attention'}
          </span>
        </div>
        <div className="coverage-grid">
          {detail.quality.coverage.map((coverage) => (
            <article key={coverage.kind} className={coverage.required && coverage.missing ? 'coverage-card missing' : 'coverage-card'}>
              <strong>{pretty(coverage.kind)}</strong>
              <span>{coverage.complete}/{coverage.expected || 0} complete</span>
              <small>
                {coverage.required ? 'Required' : 'Optional'}
                {coverage.missing ? ` · ${coverage.missing} missing` : ''}
                {coverage.placeholders ? ` · ${coverage.placeholders} in progress` : ''}
              </small>
            </article>
          ))}
        </div>
        {detail.quality.warnings.length > 0 && (
          <ul className="quality-warnings">
            {detail.quality.warnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        )}
      </section>

      <section className="panel review-action-panel">
        <div>
          <h2>Editorial decision</h2>
          <p className="muted">
            Every decision is recorded in the audit trail. Flagging and change requests require a reason.
          </p>
        </div>
        <form
          className="review-form"
          onSubmit={(event) => {
            event.preventDefault()
            if (requiresNote && !note.trim()) return
            onReview(action, note.trim())
          }}
        >
          <label className="field">
            <span>Decision</span>
            <select value={action} onChange={(event) => setAction(event.target.value as ReviewAction)} disabled={saving}>
              {availableActions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label className="field review-note-field">
            <span>Review note {requiresNote ? '(required)' : '(optional)'}</span>
            <textarea
              rows={3}
              maxLength={2000}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder={requiresNote ? 'Explain what needs attention…' : 'Add context for this decision…'}
              disabled={saving}
            />
          </label>
          <div className="form-actions">
            <button
              type="submit"
              className="btn primary"
              disabled={saving || (requiresNote && !note.trim()) || approveBlocked}
            >
              {saving ? 'Saving…' : selectedAction?.label ?? 'Save decision'}
            </button>
            <p className="field-hint">
              Current decision: {REVIEW_LABELS[detail.review.status]}
              {detail.review.reviewed_at ? ` · ${new Date(detail.review.reviewed_at).toLocaleString()}` : ''}
              {approveBlocked
                ? ' · Approval needs an unflagged ready story with complete required assets.'
                : ''}
            </p>
          </div>
        </form>
      </section>

      <section className="panel">
        <h2>Current-version assets</h2>
        {!detail.assets.length && <p className="muted">No assets have been produced for this version yet.</p>}
        {Object.entries(assetsByKind).map(([kind, assets]) => (
          <div className="asset-review-group" key={kind}>
            <h3>{pretty(kind)} <span className="muted">({assets?.length ?? 0})</span></h3>
            <div className="asset-review-list">
              {(assets ?? []).map((asset) => (
                <article className="asset-review" key={asset.id}>
                  <AssetPreview asset={asset} />
                  <p className="muted">
                    {asset.scene_id ? `Scene ${asset.scene_id}` : asset.line_id ? `Line ${asset.line_id}` : pretty(asset.kind)}
                    {asset.duration_ms ? ` · ${(asset.duration_ms / 1000).toFixed(1)}s` : ''}
                  </p>
                </article>
              ))}
            </div>
          </div>
        ))}
      </section>

      <section className="panel review-script-panel">
        <h2>Script and scene context</h2>
        {!detail.state && <p className="muted">This version does not yet have a readable story state.</p>}
        {scenes.length > 0 && (
          <div className="review-scenes">
            {scenes.map((scene, index) => (
              <article key={String(scene.id ?? index)}>
                <strong>{String(scene.title ?? `Scene ${index + 1}`)}</strong>
                {typeof scene.summary === 'string' && <p>{scene.summary}</p>}
              </article>
            ))}
          </div>
        )}
        {lines.length > 0 && (
          <ol className="review-script">
            {lines.map((line, index) => (
              <li key={String(line.id ?? index)}>
                <strong>{String(line.speaker ?? 'Narrator')}</strong>
                <span>{String(line.text ?? '')}</span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {detail.review_history.length > 0 && (
        <section className="panel">
          <h2>Review history</h2>
          <ul className="review-history">
            {detail.review_history.map((entry) => (
              <li key={entry.id}>
                <div>
                  <strong>{pretty(entry.action)}</strong>
                  <span className="muted"> · {entry.actor_user_id ?? 'system'} · {new Date(entry.created_at).toLocaleString()}</span>
                </div>
                {entry.metadata?.note && <p>{String(entry.metadata.note)}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

export function StoriesTab({ onError }: StoriesTabProps) {
  const [stories, setStories] = useState<AdminStory[]>([])
  const [detail, setDetail] = useState<AdminStoryDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [openingStoryId, setOpeningStoryId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [query, setQuery] = useState('')
  const [reviewFilter, setReviewFilter] = useState<'all' | ReviewStatus>('all')
  const [qualityFilter, setQualityFilter] = useState<'all' | 'ready' | 'attention'>('all')

  const load = useCallback(async () => {
    setLoading(true)
    onError(null)
    try {
      const next = await apiFetch<AdminStory[]>('/admin/stories')
      setStories(next)
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Unable to load review queue')
    } finally {
      setLoading(false)
    }
  }, [onError])

  useEffect(() => {
    void load()
  }, [load])

  const openStory = useCallback(async (storyId: string) => {
    setOpeningStoryId(storyId)
    onError(null)
    try {
      setDetail(await apiFetch<AdminStoryDetail>(`/admin/stories/${storyId}`))
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Unable to load story review details')
    } finally {
      setOpeningStoryId(null)
    }
  }, [onError])

  const saveReview = useCallback(async (action: ReviewAction, note: string) => {
    if (!detail) return
    setSaving(true)
    onError(null)
    try {
      const updated = await apiFetch<AdminStory>(`/admin/stories/${detail.id}/review`, {
        method: 'POST',
        body: JSON.stringify({ action, note: note || null }),
      })
      setStories((current) => current.map((story) => story.id === updated.id ? updated : story))
      try {
        setDetail(await apiFetch<AdminStoryDetail>(`/admin/stories/${detail.id}`))
      } catch {
        // The write has already succeeded; retain the fresh summary while a later
        // refresh reloads the full history rather than pretending the decision failed.
        setDetail((current) => current ? { ...current, ...updated } : current)
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Unable to save review decision')
    } finally {
      setSaving(false)
    }
  }, [detail, onError])

  const filteredStories = useMemo(() => {
    const search = query.trim().toLocaleLowerCase()
    return stories.filter((story) => {
      if (reviewFilter !== 'all' && story.review.status !== reviewFilter) return false
      if (qualityFilter === 'ready' && !story.quality.ready_for_review) return false
      if (qualityFilter === 'attention' && story.quality.ready_for_review) return false
      if (!search) return true
      return [story.title, story.owner?.email, story.status, story.id]
        .filter(Boolean)
        .join(' ')
        .toLocaleLowerCase()
        .includes(search)
    })
  }, [qualityFilter, query, reviewFilter, stories])

  if (detail) {
    return (
      <StoryDetailPanel
        key={`${detail.id}-${detail.review.reviewed_at ?? 'unreviewed'}`}
        detail={detail}
        saving={saving}
        onBack={() => {
          setDetail(null)
          void load()
        }}
        onReview={saveReview}
      />
    )
  }

  return (
    <section className="panel review-queue">
      <div className="panel-head">
        <div>
          <h2>Content review queue</h2>
          <p className="muted">Review the current version, its production coverage, and its editorial decision.</p>
        </div>
        <button type="button" className="btn ghost" onClick={() => void load()} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
      <div className="filter-bar" aria-label="Filter content review queue">
        <label className="field filter-search">
          <span className="sr-only">Search stories</span>
          <input value={query} type="search" placeholder="Search story or owner" onChange={(event) => setQuery(event.target.value)} />
        </label>
        <label className="field filter-select">
          <span className="sr-only">Review status</span>
          <select value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value as 'all' | ReviewStatus)}>
            <option value="all">All review states</option>
            {Object.entries(REVIEW_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="field filter-select">
          <span className="sr-only">Asset coverage</span>
          <select value={qualityFilter} onChange={(event) => setQualityFilter(event.target.value as 'all' | 'ready' | 'attention')}>
            <option value="all">All quality states</option>
            <option value="ready">Ready for review</option>
            <option value="attention">Needs attention</option>
          </select>
        </label>
        <p className="filter-count" aria-live="polite">{filteredStories.length} of {stories.length}</p>
      </div>

      {loading && <p className="muted">Loading stories…</p>}
      {!loading && !filteredStories.length && <p className="muted">No stories match these review filters.</p>}
      {!loading && filteredStories.length > 0 && (
        <div className="table-scroll">
          <table className="clickable review-table">
            <thead>
              <tr>
                <th>Story</th>
                <th>Owner</th>
                <th>Pipeline</th>
                <th>Quality</th>
                <th>Review</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {filteredStories.map((story) => (
                <tr
                  key={story.id}
                  tabIndex={0}
                  role="button"
                  aria-label={`Review ${story.title ?? 'untitled story'}`}
                  onClick={() => void openStory(story.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      void openStory(story.id)
                    }
                  }}
                >
                  <td>
                    <strong>{story.title ?? 'Untitled story'}</strong>
                    {openingStoryId === story.id && <span className="muted"> · opening…</span>}
                  </td>
                  <td className="muted">{story.owner?.email ?? '—'}</td>
                  <td><span className={`badge ${story.status}`}>{pretty(story.status)}</span></td>
                  <td>
                    <span className={story.quality.ready_for_review ? 'quality-inline ready' : 'quality-inline blocked'}>
                      {qualityCopy(story.quality)}
                    </span>
                  </td>
                  <td><ReviewBadge status={story.review.status} /></td>
                  <td className="muted">{new Date(story.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
