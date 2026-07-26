type Props = {
  name: string
  role: string
  size?: 'sm' | 'md'
}

const ROLE_COLORS: Record<string, { bg: string; fg: string }> = {
  narrator: { bg: '#e0f2f1', fg: '#00796b' },
  protagonist: { bg: '#e3f2fd', fg: '#1565c0' },
  antagonist: { bg: '#fce4ec', fg: '#c62828' },
  supporting: { bg: '#ede7f6', fg: '#5e35b1' },
}

const ROLE_ICONS: Record<string, string> = {
  narrator: '\u{1F399}',   // studio microphone
  protagonist: '\u2605',   // star
  antagonist: '\u2666',    // diamond
  supporting: '\u25CF',    // circle
}

const SIZES = { sm: 28, md: 40 } as const

/**
 * A colored circle showing the first grapheme of a character's name.
 * Works across all scripts (Latin, Devanagari, Tamil, Arabic, etc.)
 * because it uses `Intl.Segmenter` where available, falling back to
 * the first code-point otherwise.
 */
export function CharacterAvatar({ name, role, size = 'md' }: Props) {
  const px = SIZES[size]
  const colors = ROLE_COLORS[role] ?? ROLE_COLORS.supporting
  const initial = firstGrapheme(name)
  const fontSize = size === 'sm' ? '0.78rem' : '1.05rem'
  const badgeSize = size === 'sm' ? 12 : 16

  return (
    <span
      className="char-avatar"
      aria-label={`${name}, ${role.replaceAll('_', ' ')}`}
      style={{
        width: px,
        height: px,
        minWidth: px,
        borderRadius: '50%',
        background: colors.bg,
        color: colors.fg,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize,
        fontWeight: 700,
        lineHeight: 1,
        position: 'relative',
        userSelect: 'none',
      }}
    >
      {initial}
      <span
        className="char-avatar-badge"
        style={{
          position: 'absolute',
          bottom: -2,
          right: -2,
          width: badgeSize,
          height: badgeSize,
          borderRadius: '50%',
          background: colors.fg,
          color: '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: size === 'sm' ? '0.45rem' : '0.55rem',
          lineHeight: 1,
          border: '1.5px solid #fff',
        }}
      >
        {ROLE_ICONS[role] ?? ROLE_ICONS.supporting}
      </span>
    </span>
  )
}

function firstGrapheme(s: string): string {
  const trimmed = s.trim()
  if (!trimmed) return '?'
  // Intl.Segmenter correctly splits multi-codepoint graphemes
  // (Devanagari conjuncts, emoji, etc.)
  if (typeof Intl !== 'undefined' && 'Segmenter' in Intl) {
    const seg = new Intl.Segmenter(undefined, { granularity: 'grapheme' })
    const first = seg.segment(trimmed)[Symbol.iterator]().next()
    if (!first.done) return first.value.segment
  }
  // Fallback: Array.from splits on code points, good enough for most scripts
  return Array.from(trimmed)[0] ?? '?'
}
