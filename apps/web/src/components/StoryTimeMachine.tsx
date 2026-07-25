import { useEffect, useMemo, useState, type FormEvent } from 'react'
import type { DialogueLine, Scene, Version } from '../types'

/** Kept below the API's 500-character directive cap once the scene context is
 * added. The server remains the authority and validates the final request. */
const MAX_ALTERNATE_DECISION_CHARS = 180

type Props = {
  scenes: Scene[]
  lines: DialogueLine[]
  versions: Version[]
  currentVersionId: string | null | undefined
  baseVersionId: string | null | undefined
  loadingVersion?: boolean
  disabled?: boolean
  onSelectBaseVersion: (versionId: string) => void
  /** The parent owns the API call so this component stays useful as a purely
   * presentational, accessible timeline. */
  onCreateBranch: (
    sceneId: string,
    instructionDelta: string,
    baseVersionId: string,
  ) => Promise<void>
}

function compact(value: string, max: number): string {
  const normalized = value.replace(/\s+/g, ' ').trim()
  return normalized.length <= max ? normalized : `${normalized.slice(0, Math.max(0, max - 1)).trimEnd()}…`
}

/**
 * The explicit regenerate endpoint intentionally accepts a short creative
 * direction, not a UI-specific payload. Keeping the causal instruction here
 * makes a scene selection deterministic and works for the web app, demo, and
 * any future native client using the same endpoint.
 */
function buildSceneBranchInstruction(scene: Scene, alternateDecision: string): string {
  const title = compact(scene.title || `Scene ${scene.index + 1}`, 64)
  const decision = compact(alternateDecision, MAX_ALTERNATE_DECISION_CHARS)
  return [
    `Rewrite from Scene ${scene.index + 1} (“${title}”).`,
    'Keep all earlier events, facts, character knowledge, and relationships unchanged.',
    `Alternate decision: ${decision}`,
    'Rebuild this scene and every later event as one coherent causal future. Do not mention this instruction.',
  ].join(' ')
}

function parentLabel(version: Version, byId: Map<string, Version>): string {
  if (!version.parent_version_id) return 'original'
  const parent = byId.get(version.parent_version_id)
  return parent ? `from v${parent.version_number}` : 'from an earlier version'
}

export function StoryTimeMachine({
  scenes,
  lines,
  versions,
  currentVersionId,
  baseVersionId,
  loadingVersion = false,
  disabled = false,
  onSelectBaseVersion,
  onCreateBranch,
}: Props) {
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null)
  const [alternateDecision, setAlternateDecision] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const selectedScene = scenes.find((scene) => scene.id === selectedSceneId) ?? scenes[0] ?? null
  const selectionLocked = disabled || submitting || loadingVersion
  const versionsById = useMemo(() => new Map(versions.map((version) => [version.id, version])), [versions])

  useEffect(() => {
    setSelectedSceneId(null)
    setAlternateDecision('')
  }, [baseVersionId])

  async function submitBranch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedScene || !baseVersionId || !alternateDecision.trim() || selectionLocked) return

    setSubmitting(true)
    try {
      await onCreateBranch(
        selectedScene.id,
        buildSceneBranchInstruction(selectedScene, alternateDecision),
        baseVersionId,
      )
      setAlternateDecision('')
    } catch {
      // Studio owns request errors so it can show them alongside the pipeline
      // progress. Keeping the draft lets the listener adjust and retry.
    } finally {
      setSubmitting(false)
    }
  }

  if (!selectedScene) return null

  return (
    <section className="time-machine-panel" aria-labelledby="time-machine-title">
      <div className="time-machine-head">
        <div>
          <p className="eyebrow">Story Time Machine</p>
          <h2 id="time-machine-title">Change one turn. Rebuild the future.</h2>
        </div>
        <p className="time-machine-copy">
          Pick a version, choose a scene or line, and create a sibling future. Every prior
          version stays playable in the revision history.
        </p>
      </div>

      {versions.length > 0 && (
        <ol className="revision-branch" aria-label="Story version history">
          {versions.map((version) => {
            const current = version.id === currentVersionId
            const selected = version.id === baseVersionId
            return (
              <li key={version.id} className={selected ? 'current' : ''}>
                <button
                  type="button"
                  disabled={selectionLocked}
                  aria-pressed={selected}
                  onClick={() => onSelectBaseVersion(version.id)}
                >
                  <span>v{version.version_number}</span>
                  <small>{parentLabel(version, versionsById)}</small>
                  {current && <strong>Current</strong>}
                  {selected && !current && <strong>Branch source</strong>}
                </button>
              </li>
            )
          })}
        </ol>
      )}

      <ol className="story-timeline" aria-label="Choose a moment in the story">
        {scenes.map((scene) => {
          const selected = scene.id === selectedScene.id
          const sceneLines = lines.filter((line) => line.scene_id === scene.id)
          return (
            <li key={scene.id} className={selected ? 'selected' : ''}>
              <button
                type="button"
                className="timeline-scene"
                aria-pressed={selected}
                disabled={selectionLocked}
                onClick={() => setSelectedSceneId(scene.id)}
              >
                <span className="timeline-marker" aria-hidden="true">
                  {scene.index + 1}
                </span>
                <span>
                  <strong>{scene.title}</strong>
                  <small>{scene.summary}</small>
                </span>
              </button>
              {sceneLines.length > 0 && (
                <ul className="timeline-lines" aria-label={`Lines in Scene ${scene.index + 1}`}>
                  {sceneLines.map((line) => (
                    <li key={line.id}>
                      <button
                        type="button"
                        className="timeline-line"
                        disabled={selectionLocked}
                        aria-label={`Choose Scene ${scene.index + 1} from ${line.speaker}'s line: ${compact(line.text, 90)}`}
                        onClick={() => setSelectedSceneId(scene.id)}
                      >
                        <strong>{line.speaker}</strong>
                        <span>{line.text}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          )
        })}
      </ol>

      <form className="time-machine-form" onSubmit={submitBranch}>
        <label htmlFor="alternate-decision">
          {loadingVersion
            ? 'Loading this version…'
            : `What changes in Scene ${selectedScene.index + 1}?`}
        </label>
        <textarea
          id="alternate-decision"
          value={alternateDecision}
          onChange={(event) => setAlternateDecision(event.target.value)}
          maxLength={MAX_ALTERNATE_DECISION_CHARS}
          disabled={selectionLocked}
          placeholder={`Instead of ${compact(selectedScene.summary, 120).toLowerCase()}, …`}
          required
        />
        <div className="time-machine-actions">
          <p>
            The story before this scene is kept. Scene {selectedScene.index + 1} and every later
            event will be regenerated into a new child version.
          </p>
          <button
            type="submit"
            className="btn secondary"
            disabled={selectionLocked || !alternateDecision.trim()}
          >
            {submitting ? 'Creating future…' : `Create alternate future from Scene ${selectedScene.index + 1}`}
          </button>
        </div>
      </form>
    </section>
  )
}
