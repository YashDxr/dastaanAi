import { useState } from 'react'
import { formatError, stories as storiesApi } from '../api'
import type { Scene, StoryState } from '../types'

type EndingChoice = {
  id: 'hopeful' | 'bittersweet' | 'surprising'
  title: string
  description: string
  direction: string
}

/**
 * These are deliberately fixed, rather than a free-text prompt. The target
 * scene anchors the fork in the already-validated story state and every option
 * gives the model a bounded creative direction from that point onward.
 */
const ENDING_CHOICES: readonly EndingChoice[] = [
  {
    id: 'hopeful',
    title: 'Hopeful resolution',
    description: 'Turn the final conflict into a hard-won, uplifting close.',
    direction:
      'Resolve the remaining story with a hopeful, earned outcome. Let courage, compassion, or cooperation address the central conflict.',
  },
  {
    id: 'bittersweet',
    title: 'Bittersweet resolution',
    description: 'Keep the victory meaningful, while allowing a real cost to remain.',
    direction:
      'Resolve the remaining story with a meaningful but bittersweet outcome. The ending should leave a clear emotional cost while still giving the central conflict a satisfying resolution.',
  },
  {
    id: 'surprising',
    title: 'Unexpected reveal',
    description: 'Land on a plausible revelation that changes the meaning of the finale.',
    direction:
      'Resolve the remaining story through one surprising but plausible revelation. It must fit the established facts and make the final events feel newly meaningful rather than arbitrary.',
  },
]

function instructionFor(choice: EndingChoice, sceneNumber: number): string {
  return [
    `Use scene ${sceneNumber} as the branch point and create an alternate ending from that scene forward.`,
    `Preserve all events, characters, relationships, setting, and established facts before scene ${sceneNumber}.`,
    'Keep the existing tone and named characters; do not add real people, copyrighted characters, lyrics, or meta-commentary.',
    choice.direction,
  ].join(' ')
}

function secondToLastScene(state?: StoryState | null): Scene | null {
  if (!state || state.scenes.length < 2) return null
  // Scene ids are stable across a version and `index` is the canonical order.
  // Sorting defensively means a partially reordered response cannot branch from
  // the wrong moment in the story.
  return [...state.scenes].sort((left, right) => left.index - right.index).at(-2) ?? null
}

type Props = {
  storyId: string
  state?: StoryState | null
  /** Disable alongside any other studio mutation, preventing competing forks. */
  disabled?: boolean
  /** Refresh the studio after the API has accepted the child-version fork. */
  onRequested?: () => void | Promise<void>
}

/**
 * A button-driven form of the existing scene-scoped regeneration API. There is
 * no alternate-endings pipeline: each choice creates an ordinary child version
 * and re-enters at story understanding from the penultimate scene.
 */
export function AlternateEndings({ storyId, state, disabled = false, onRequested }: Props) {
  const pivot = secondToLastScene(state)
  const [pending, setPending] = useState<EndingChoice['id'] | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (!pivot) return null

  const locked = disabled || pending !== null

  async function chooseEnding(choice: EndingChoice) {
    if (locked) return

    setPending(choice.id)
    setError(null)
    try {
      await storiesApi.regenerate(storyId, {
        scope: 'scene',
        target_stage: 'story_understanding',
        target_id: pivot.id,
        instruction_delta: instructionFor(choice, pivot.index + 1),
      })
      await onRequested?.()
    } catch (err) {
      setError(formatError(err))
    } finally {
      setPending(null)
    }
  }

  return (
    <section className="alternate-endings-panel" aria-labelledby="alternate-endings-title">
      <p className="eyebrow">Story time machine</p>
      <h2 id="alternate-endings-title">Try a different ending</h2>
      <p className="muted">
        Branch from Scene {pivot.index + 1}, <strong>{pivot.title}</strong>. Daastaan keeps the
        story so far and rebuilds every later event as a new version.
      </p>
      <ul className="alternate-endings-list">
        {ENDING_CHOICES.map((choice) => {
          const isPending = pending === choice.id
          return (
            <li key={choice.id}>
              <button
                type="button"
                className="alternate-ending-choice"
                disabled={locked}
                onClick={() => void chooseEnding(choice)}
                aria-describedby={`ending-${choice.id}-description`}
              >
                <span>{isPending ? 'Creating alternate ending…' : choice.title}</span>
                <small id={`ending-${choice.id}-description`}>{choice.description}</small>
              </button>
            </li>
          )
        })}
      </ul>
      <p className="alternate-endings-status" aria-live="polite">
        {pending ? 'Creating a new story version from this point…' : 'Choose one ending to branch.'}
      </p>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
