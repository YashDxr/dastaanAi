import { useState } from 'react'

type Props = {
  onSubmit: (text: string) => Promise<void>
  disabled?: boolean
}

export function FeedbackComposer({ onSubmit, disabled }: Props) {
  const [text, setText] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  return (
    <section className="feedback-panel">
      <p className="eyebrow">Reshape</p>
      <h2>Tell Daastaan what to change</h2>
      <p className="muted">
        Natural language works. Try “make the narrator quieter” or “give the woman more urgency.”
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (!text.trim() || pending || disabled) return
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
          disabled={disabled || pending}
        />
        <div className="feedback-row">
          <span className="char-count">{text.length}/500</span>
          <button type="submit" className="btn primary" disabled={disabled || pending || text.trim().length < 3}>
            {pending ? 'Sending…' : 'Apply feedback'}
          </button>
        </div>
      </form>
      {error && <p className="form-error">{error}</p>}
    </section>
  )
}
