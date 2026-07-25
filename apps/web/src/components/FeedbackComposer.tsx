import { useState } from 'react'
import type { FeedbackEntry, RegenDirective, StoryState } from '../types'

type Props = {
  onSubmit: (text: string) => Promise<void>
  disabled?: boolean
  /** A note is in flight and the model has not decided what it means yet. */
  interpreting?: boolean
  history?: FeedbackEntry[]
  state?: StoryState | null
}

const EXAMPLES = [
  'Make the narrator quieter and slower',
  'Give the woman more urgency in the final scene',
  'The opening feels too cheerful for this story',
]

/** What each entry point actually rewrites, in the user's language. */
const STAGE_VERB: Record<string, string> = {
  tts_synthesis: 'Re-recording',
  emotion_tagging: 'Re-reading the emotion of',
  voice_assignment: 'Re-casting the voice of',
  image_generation: 'Re-illustrating',
  story_understanding: 'Rewriting',
  mood_classification: 'Re-reading the mood of',
}

function truncate(text: string, max = 60) {
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text
}

function targetPhrase(directive: RegenDirective, state?: StoryState | null): string {
  const { scope, target_id } = directive
  if (scope === 'full_story' || !target_id) return 'the whole episode'

  if (scope === 'line') {
    const line = state?.lines.find((l) => l.id === target_id)
    if (!line) return 'one line'
    return `${line.speaker}'s line "${truncate(line.text)}"`
  }
  if (scope === 'character') {
    const character = state?.characters.find((c) => c.id === target_id)
    return character ? character.name : 'one character'
  }
  if (scope === 'scene') {
    const scene = state?.scenes.find((s) => s.id === target_id)
    return scene ? `the scene "${scene.title}"` : 'one scene'
  }
  return 'the whole episode'
}

function describeDirective(
  directive: RegenDirective | null,
  state?: StoryState | null,
): string | null {
  if (!directive?.target_stage) return null
  const verb = STAGE_VERB[directive.target_stage] ?? 'Regenerating'
  return `${verb} ${targetPhrase(directive, state)}`
}

function HistoryItem({ entry, state }: { entry: FeedbackEntry; state?: StoryState | null }) {
  const summary = describeDirective(entry.directive_json, state)
  return (
    <li className={`revision ${entry.status}`}>
      <p className="revision-note">"{entry.raw_text}"</p>
      {entry.status === 'applied' && summary && <p className="revision-outcome">{summary}</p>}
      {entry.status === 'pending' && <p className="revision-outcome muted">Interpreting…</p>}
      {entry.status === 'failed' && (
        <p className="revision-outcome failed">
          Couldn't apply this: {entry.error ?? 'the note could not be interpreted.'}
        </p>
      )}
      <span className="revision-time">{new Date(entry.created_at).toLocaleString()}</span>
    </li>
  )
}

export function FeedbackComposer({
  onSubmit,
  disabled,
  interpreting,
  history = [],
  state,
}: Props) {
  const [text, setText] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const busy = pending || interpreting
  const locked = disabled || busy

  return (
    <section className="feedback-panel">
      <p className="eyebrow">Reshape</p>
      <h2>Tell Daastaan what to change</h2>
      <p className="muted">
        Natural language works. Daastaan picks the narrowest change that satisfies your note and
        keeps everything else exactly as it is.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (!text.trim() || locked) return
          setPending(true)
          setError(null)
          void onSubmit(text.trim())
            .then(() => setText(''))
            .catch((err: Error) => setError(err.message))
            .finally(() => setPending(false))
        }}
      >
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={500}
          rows={3}
          placeholder="Describe the change…"
          disabled={locked}
        />
        <ul className="example-chips">
          {EXAMPLES.map((example) => (
            <li key={example}>
              <button
                type="button"
                className="chip"
                disabled={locked}
                onClick={() => setText(example)}
              >
                {example}
              </button>
            </li>
          ))}
        </ul>
        <div className="feedback-row">
          <span className="char-count">{text.length}/500</span>
          <button
            type="submit"
            className="btn primary"
            disabled={locked || text.trim().length < 3}
          >
            {busy ? 'Working…' : 'Apply feedback'}
          </button>
        </div>
      </form>
      {interpreting && (
        <p className="muted interpreting">
          Reading your note and deciding what to change…
        </p>
      )}
      {error && <p className="form-error">{error}</p>}

      {history.length > 0 && (
        <div className="revision-log">
          <p className="eyebrow">Revision history</p>
          <ul>
            {history.map((entry) => (
              <HistoryItem key={entry.id} entry={entry} state={state} />
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
