import { useCallback, useMemo, useState } from 'react'
import { AppHeader } from '../components/AppHeader'
import { UploadDropzone } from '../components/UploadDropzone'
import { formatError, stories as storiesApi } from '../api'
import type { Ingest, User } from '../types'

// Mirrors `limits.MAX_STORY_INPUT_CHARS` / `MIN` on the server, which rejects
// anything outside this range. Keep the two in step: the counter here is a
// courtesy, the server limit is the real one.
const MAX_CHARS = 24000
const MIN_CHARS = 20

const LANGUAGE_GROUPS = [
  {
    label: null,
    languages: [
      { code: 'en', label: 'English' },
      { code: 'es', label: 'Español' },
      { code: 'fr', label: 'Français' },
      { code: 'de', label: 'Deutsch' },
      { code: 'ja', label: '日本語' },
    ],
  },
  {
    label: 'Indian',
    languages: [
      { code: 'hi', label: 'हिन्दी' },
      { code: 'bn', label: 'বাংলা' },
      { code: 'ta', label: 'தமிழ்' },
      { code: 'te', label: 'తెలుగు' },
      { code: 'kn', label: 'ಕನ್ನಡ' },
      { code: 'ml', label: 'മലയാളം' },
      { code: 'mr', label: 'मराठी' },
      { code: 'gu', label: 'ગુજરાતી' },
      { code: 'pa', label: 'ਪੰਜਾਬੀ' },
      { code: 'or', label: 'ଓଡ଼ିଆ' },
      { code: 'ur', label: 'اردو' },
    ],
  },
] as const

type Props = {
  user: User
  onLogout: () => void
  onHome: () => void
  onCreated: (storyId: string) => void
}

export function Compose({ user, onLogout, onHome, onCreated }: Props) {
  const [text, setText] = useState('')
  const [genre, setGenre] = useState('')
  const [language, setLanguage] = useState('en')
  const [outputFormat, setOutputFormat] = useState<'audio' | 'video' | 'both'>('audio')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [imported, setImported] = useState<string | null>(null)

  const remaining = MAX_CHARS - text.length
  const ready = text.trim().length >= MIN_CHARS && !pending

  const hint = useMemo(() => {
    const n = text.trim().length
    if (n === 0) return 'Start with the feeling, not the plot.'
    if (n < MIN_CHARS) return `${MIN_CHARS - n} more characters to begin.`
    return 'Enough to cast. You can reshape later.'
  }, [text])

  // The extracted text lands in the textarea rather than going straight to the
  // pipeline. OCR and condensing can both go wrong in ways only the person who
  // uploaded the file can see, and this is the last point before it costs money.
  const onExtracted = useCallback((result: Ingest) => {
    setText((result.cleaned_text ?? '').slice(0, MAX_CHARS))
    setImported(result.notes)
    setError(null)
    setGenre((current) => current || result.genre_hint || '')
  }, [])

  return (
    <div className="app-frame">
      <AppHeader
        email={user.email}
        onLogout={onLogout}
        onHome={onHome}
        onCompose={() => undefined}
        compact
      />
      <main className="compose">
        <section className="compose-copy">
          <p className="eyebrow">Compose</p>
          <h1>What should we stage tonight?</h1>
          <p className="lede">
            Write freely, or bring a file. Daastaan will find the mood, cast the voices, and
            mix the episode.
          </p>
        </section>

        <form
          className="compose-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (!ready) return
            setPending(true)
            setError(null)
            void storiesApi
              .create(text.trim(), genre.trim() || undefined, outputFormat, language)
              .then((res) => onCreated(res.story_id))
              .catch((err) => setError(formatError(err)))
              .finally(() => setPending(false))
          }}
        >
          <UploadDropzone disabled={pending} language={language} onExtracted={onExtracted} />

          <div className="compose-divider">
            <span>or write it yourself</span>
          </div>

          <label className="compose-label">
            Your dream or memory
            <textarea
              value={text}
              onChange={(e) => {
                setText(e.target.value.slice(0, MAX_CHARS))
                setImported(null)
              }}
              rows={12}
              placeholder="I woke up still hearing the rain on the tin roof…"
              required
              minLength={MIN_CHARS}
            />
          </label>
          {imported && <p className="import-note">{imported} Edit anything before generating.</p>}
          <div className="compose-meta">
            <p className="hint">{hint}</p>
            <p className={`char-count ${remaining < 200 ? 'warn' : ''}`}>{remaining} left</p>
          </div>

          <label className="compose-label slim">
            Genre hint <span className="optional">(optional)</span>
            <input
              value={genre}
              onChange={(e) => setGenre(e.target.value.slice(0, 60))}
              placeholder="Noir fable, quiet sci-fi, family memory…"
              maxLength={60}
            />
          </label>

          <div className="format-selector-group">
            <label className="compose-label slim">
              Language
            </label>
            {LANGUAGE_GROUPS.map((group) => (
              <div key={group.label ?? 'global'} className="lang-group">
                {group.label && <span className="lang-group-label">{group.label}</span>}
                <div className="format-selector wrap">
                  {group.languages.map((lang) => (
                    <button
                      key={lang.code}
                      type="button"
                      className={`chip${language === lang.code ? ' active' : ''}`}
                      onClick={() => setLanguage(lang.code)}
                      disabled={pending}
                    >
                      {lang.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="format-selector-group">
            <label className="compose-label slim">
              Output format
            </label>
            <div className="format-selector">
              {(['audio', 'video', 'both'] as const).map((fmt) => (
                <button
                  key={fmt}
                  type="button"
                  className={`chip${outputFormat === fmt ? ' active' : ''}`}
                  onClick={() => setOutputFormat(fmt)}
                  disabled={pending}
                >
                  {fmt === 'audio' ? 'Audio only' : fmt === 'video' ? 'Video' : 'Both'}
                </button>
              ))}
            </div>
            {outputFormat !== 'audio' && (
              <p className="format-hint">Video adds scene artwork — uses more credits.</p>
            )}
          </div>

          {error && <p className="form-error">{error}</p>}

          <div className="compose-actions">
            <button type="button" className="btn ghost" onClick={onHome} disabled={pending}>
              Cancel
            </button>
            <button type="submit" className="btn primary" disabled={!ready}>
              {pending ? 'Sending to studio…' : 'Generate episode'}
            </button>
          </div>
        </form>
      </main>
    </div>
  )
}
