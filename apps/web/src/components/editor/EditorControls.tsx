/**
 * The control stack: format, captions, audio, and titles.
 *
 * Every control edits the manifest through one `onChange`, and the manifest is
 * the only state. There is no separate "applied" step, and no local copy of a
 * slider's value that could disagree with what will be rendered — the preview and
 * the export read the same object.
 */

import { useRef, useState } from 'react'
import { formatError, editor as editorApi } from '../../api'
import type {
  AspectOption,
  CaptionPreset,
  CaptionStyle,
  EditorCapabilities,
  EditorOption,
  LocalAudio,
  VideoEditManifest,
} from '../../types'
import { clock } from './EditorPreview'

type Props = {
  storyId: string
  manifest: VideoEditManifest
  capabilities: EditorCapabilities
  aspects: AspectOption[]
  fonts: EditorOption[]
  presets: CaptionPreset[]
  tracks: LocalAudio[]
  onChange: (next: VideoEditManifest) => void
  onTracksChange: (tracks: LocalAudio[]) => void
}

export function EditorControls({
  storyId,
  manifest,
  capabilities,
  aspects,
  fonts,
  presets,
  tracks,
  onChange,
  onTracksChange,
}: Props) {
  const caption = manifest.caption
  const audio = manifest.audio

  const patch = (partial: Partial<VideoEditManifest>) => onChange({ ...manifest, ...partial })
  const patchCaption = (partial: Partial<CaptionStyle>) =>
    patch({ caption: { ...caption, ...partial } })
  const patchAudio = (partial: Partial<VideoEditManifest['audio']>) =>
    patch({ audio: { ...audio, ...partial } })

  return (
    <div className="editor-controls">
      <section className="editor-panel">
        <p className="eyebrow">Format</p>
        <div className="editor-chips">
          {aspects.map((option) => (
            <button
              key={option.key}
              type="button"
              className={`editor-chip${manifest.aspect === option.key ? ' active' : ''}`}
              title={`${option.detail} (${option.width}×${option.height})`}
              onClick={() => patch({ aspect: option.key as VideoEditManifest['aspect'] })}
            >
              {option.label}
              <span className="editor-chip-note">{option.key}</span>
            </button>
          ))}
        </div>

        <Choice
          label="Artwork is square, so a wide or tall frame has to give something up"
          value={manifest.frame_fill}
          options={[
            ['crop', 'Fill the frame'],
            ['blur', 'Keep it all, blur the margins'],
          ]}
          onChange={(value) => patch({ frame_fill: value as VideoEditManifest['frame_fill'] })}
        />

        <Toggle
          label="Slow zoom on each image"
          checked={manifest.motion.ken_burns}
          onChange={(ken_burns) => patch({ motion: { ...manifest.motion, ken_burns } })}
        />
        <Slider
          label="Crossfade"
          value={manifest.motion.transition_ms}
          min={0}
          max={2000}
          step={100}
          format={(value) => (value === 0 ? 'Hard cut' : `${(value / 1000).toFixed(1)}s`)}
          onChange={(transition_ms) => patch({ motion: { ...manifest.motion, transition_ms } })}
        />
      </section>

      <section className="editor-panel">
        <p className="eyebrow">Captions</p>
        <div className="editor-chips">
          {presets.map((preset) => (
            <button
              key={preset.key}
              type="button"
              className={`editor-chip${matchesPreset(caption, preset.style) ? ' active' : ''}`}
              title={preset.detail}
              onClick={() => patch({ caption: preset.style })}
            >
              {preset.label}
            </button>
          ))}
        </div>

        {caption.enabled && (
          <>
            <Select
              label="Font"
              value={caption.font}
              options={fonts.map((font) => [font.key, font.label])}
              onChange={(font) => patchCaption({ font: font as CaptionStyle['font'] })}
            />
            <Slider
              label="Size"
              value={caption.size_pt}
              min={12}
              max={96}
              step={1}
              format={(value) => `${value}px`}
              onChange={(size_pt) => patchCaption({ size_pt })}
            />
            <Choice
              label="Position"
              value={caption.position}
              options={[
                ['top', 'Top'],
                ['middle', 'Middle'],
                ['bottom', 'Bottom'],
              ]}
              onChange={(position) =>
                patchCaption({ position: position as CaptionStyle['position'] })
              }
            />
            {caption.position !== 'middle' && (
              <Slider
                label="Distance from edge"
                value={caption.margin_px}
                min={0}
                max={400}
                step={4}
                format={(value) => `${value}px`}
                onChange={(margin_px) => patchCaption({ margin_px })}
              />
            )}
            <div className="editor-row">
              <Colour
                label="Text"
                value={caption.primary_color}
                onChange={(primary_color) => patchCaption({ primary_color })}
              />
              <Colour
                label="Outline and box"
                value={caption.outline_color}
                onChange={(outline_color) => patchCaption({ outline_color })}
              />
            </div>
            <Slider
              label="Outline"
              value={caption.outline_px}
              min={0}
              max={12}
              step={1}
              format={(value) => (value === 0 ? 'None' : `${value}px`)}
              onChange={(outline_px) => patchCaption({ outline_px })}
            />
            <Toggle
              label="Box behind the text"
              checked={caption.box}
              onChange={(box) => patchCaption({ box })}
            />
            {caption.box && (
              <Slider
                label="Box opacity"
                value={Math.round(caption.box_opacity * 100)}
                min={0}
                max={100}
                step={5}
                format={(value) => `${value}%`}
                onChange={(value) => patchCaption({ box_opacity: value / 100 })}
              />
            )}
            <Slider
              label="Line length"
              value={caption.max_chars_per_line}
              min={16}
              max={80}
              step={1}
              format={(value) => `${value} characters`}
              onChange={(max_chars_per_line) => patchCaption({ max_chars_per_line })}
            />
            <Toggle
              label="Bold"
              checked={caption.bold}
              onChange={(bold) => patchCaption({ bold })}
            />
            <Toggle
              label="All capitals"
              checked={caption.uppercase}
              onChange={(uppercase) => patchCaption({ uppercase })}
            />
            <Toggle
              label="Show who is speaking"
              checked={caption.show_speaker}
              onChange={(show_speaker) => patchCaption({ show_speaker })}
            />
          </>
        )}
      </section>

      <section className="editor-panel">
        <p className="eyebrow">Sound</p>
        <Slider
          label="Narration"
          value={audio.narration_gain_db}
          min={-24}
          max={12}
          step={1}
          format={decibels}
          onChange={(narration_gain_db) => patchAudio({ narration_gain_db })}
        />

        {capabilities.has_score && (
          <>
            <Toggle
              label="Keep the generated score"
              checked={audio.keep_score}
              onChange={(keep_score) => patchAudio({ keep_score })}
            />
            {audio.keep_score && (
              <Slider
                label="Score"
                value={audio.score_gain_db}
                min={-24}
                max={12}
                step={1}
                format={decibels}
                onChange={(score_gain_db) => patchAudio({ score_gain_db })}
              />
            )}
            {!audio.keep_score && (
              <p className="muted editor-hint">
                The preview still plays the score, because the mix it is playing already
                contains it. Your export will not.
              </p>
            )}
          </>
        )}

        <BackingTrack
          storyId={storyId}
          tracks={tracks}
          selected={audio.local_asset_id}
          onSelect={(local_asset_id) => patchAudio({ local_asset_id })}
          onTracksChange={onTracksChange}
        />

        {audio.local_asset_id && (
          <>
            <Slider
              label="Backing track"
              value={audio.local_gain_db}
              min={-40}
              max={12}
              step={1}
              format={decibels}
              onChange={(local_gain_db) => patchAudio({ local_gain_db })}
            />
            <Slider
              label="Start the track"
              value={audio.local_offset_ms}
              min={-30000}
              max={30000}
              step={100}
              format={offset}
              onChange={(local_offset_ms) => patchAudio({ local_offset_ms })}
            />
            <Toggle
              label="Loop it to cover the whole cut"
              checked={audio.local_loop}
              onChange={(local_loop) => patchAudio({ local_loop })}
            />
          </>
        )}

        <Toggle
          label="Duck music under the narration"
          checked={audio.duck_under_narration}
          onChange={(duck_under_narration) => patchAudio({ duck_under_narration })}
        />
        <div className="editor-row">
          <Slider
            label="Fade in"
            value={audio.fade_in_ms}
            min={0}
            max={10000}
            step={250}
            format={seconds}
            onChange={(fade_in_ms) => patchAudio({ fade_in_ms })}
          />
          <Slider
            label="Fade out"
            value={audio.fade_out_ms}
            min={0}
            max={10000}
            step={250}
            format={seconds}
            onChange={(fade_out_ms) => patchAudio({ fade_out_ms })}
          />
        </div>
      </section>

      <section className="editor-panel">
        <p className="eyebrow">Opening and brand</p>
        <Toggle
          label="Open on a title card"
          checked={manifest.title_card.enabled}
          onChange={(enabled) => patch({ title_card: { ...manifest.title_card, enabled } })}
        />
        {manifest.title_card.enabled && (
          <>
            <Text
              label="Heading"
              value={manifest.title_card.heading}
              maxLength={80}
              onChange={(heading) => patch({ title_card: { ...manifest.title_card, heading } })}
            />
            <Text
              label="Subheading"
              value={manifest.title_card.subheading}
              maxLength={120}
              onChange={(subheading) =>
                patch({ title_card: { ...manifest.title_card, subheading } })
              }
            />
            <Slider
              label="Hold for"
              value={manifest.title_card.duration_ms}
              min={500}
              max={8000}
              step={250}
              format={seconds}
              onChange={(duration_ms) =>
                patch({ title_card: { ...manifest.title_card, duration_ms } })
              }
            />
          </>
        )}

        <Toggle
          label="Watermark"
          checked={manifest.watermark.enabled}
          onChange={(enabled) => patch({ watermark: { ...manifest.watermark, enabled } })}
        />
        {manifest.watermark.enabled && (
          <>
            <Text
              label="Text"
              value={manifest.watermark.text}
              maxLength={40}
              placeholder="@yourhandle"
              onChange={(text) => patch({ watermark: { ...manifest.watermark, text } })}
            />
            <Choice
              label="Corner"
              value={manifest.watermark.position}
              options={[
                ['top_left', 'Top left'],
                ['top_right', 'Top right'],
                ['bottom_left', 'Bottom left'],
                ['bottom_right', 'Bottom right'],
              ]}
              onChange={(position) =>
                patch({
                  watermark: {
                    ...manifest.watermark,
                    position: position as VideoEditManifest['watermark']['position'],
                  },
                })
              }
            />
            <Slider
              label="Opacity"
              value={Math.round(manifest.watermark.opacity * 100)}
              min={10}
              max={100}
              step={5}
              format={(value) => `${value}%`}
              onChange={(value) =>
                patch({ watermark: { ...manifest.watermark, opacity: value / 100 } })
              }
            />
          </>
        )}
      </section>
    </div>
  )
}

function BackingTrack({
  storyId,
  tracks,
  selected,
  onSelect,
  onTracksChange,
}: {
  storyId: string
  tracks: LocalAudio[]
  selected: string | null
  onSelect: (id: string | null) => void
  onTracksChange: (tracks: LocalAudio[]) => void
}) {
  const input = useRef<HTMLInputElement | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function upload(file: File) {
    setBusy(true)
    setError(null)
    try {
      const track = await editorApi.uploadAudio(storyId, file)
      onTracksChange([...tracks, track])
      // Selecting it is the only reason anyone uploads one.
      onSelect(track.id)
    } catch (err) {
      setError(formatError(err))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  return (
    <div className="editor-field">
      <span className="editor-label">Your own music or ambience</span>
      <div className="editor-tracks">
        <button
          type="button"
          className={`editor-chip${selected === null ? ' active' : ''}`}
          onClick={() => onSelect(null)}
        >
          None
        </button>
        {tracks.map((track) => (
          <button
            key={track.id}
            type="button"
            className={`editor-chip${selected === track.id ? ' active' : ''}`}
            title={track.filename}
            onClick={() => onSelect(track.id)}
          >
            {track.filename}
          </button>
        ))}
      </div>
      <button
        type="button"
        className="btn ghost small"
        disabled={busy}
        onClick={() => input.current?.click()}
      >
        {busy ? 'Uploading…' : 'Upload a track'}
      </button>
      <input
        ref={input}
        type="file"
        accept="audio/*,.mp3,.wav,.m4a,.flac,.opus,.ogg"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) void upload(file)
        }}
      />
      {error && <p className="form-error">{error}</p>}
    </div>
  )
}

// --- small controls ---------------------------------------------------------
//
// Local rather than shared: they exist to keep the panels above readable, and
// none of them is general enough to belong outside this file.

function Slider({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  format: (value: number) => string
  onChange: (value: number) => void
}) {
  return (
    <label className="editor-field">
      <span className="editor-label">
        {label}
        <span className="editor-value">{format(value)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  )
}

function Toggle({
  label,
  checked,
  disabled = false,
  onChange,
}: {
  label: string
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className={`editor-toggle${disabled ? ' disabled' : ''}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  )
}

function Choice({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: [string, string][]
  onChange: (value: string) => void
}) {
  return (
    <div className="editor-field">
      <span className="editor-label">{label}</span>
      <div className="editor-chips">
        {options.map(([key, text]) => (
          <button
            key={key}
            type="button"
            className={`editor-chip${value === key ? ' active' : ''}`}
            onClick={() => onChange(key)}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  )
}

function Select({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: [string, string][]
  onChange: (value: string) => void
}) {
  return (
    <label className="editor-field">
      <span className="editor-label">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map(([key, text]) => (
          <option key={key} value={key}>
            {text}
          </option>
        ))}
      </select>
    </label>
  )
}

function Colour({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="editor-field editor-colour">
      <span className="editor-label">{label}</span>
      <input type="color" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

function Text({
  label,
  value,
  maxLength,
  placeholder,
  onChange,
}: {
  label: string
  value: string
  maxLength: number
  placeholder?: string
  onChange: (value: string) => void
}) {
  return (
    <label className="editor-field">
      <span className="editor-label">{label}</span>
      <input
        type="text"
        value={value}
        maxLength={maxLength}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  )
}

/** True when the current style is exactly a preset, so the chip can show which
 *  one is in effect and stop showing it the moment anything is nudged. */
function matchesPreset(current: CaptionStyle, preset: CaptionStyle): boolean {
  return (Object.keys(preset) as (keyof CaptionStyle)[]).every(
    (key) => current[key] === preset[key],
  )
}

function decibels(value: number): string {
  if (value === 0) return 'As recorded'
  return `${value > 0 ? '+' : ''}${value} dB`
}

function seconds(ms: number): string {
  return ms === 0 ? 'Off' : `${(ms / 1000).toFixed(2).replace(/0$/, '')}s`
}

function offset(ms: number): string {
  if (ms === 0) return 'With the video'
  return ms > 0 ? `${clock(ms)} in` : `${clock(-ms)} into the track`
}
