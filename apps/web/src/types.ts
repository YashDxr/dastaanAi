export type User = {
  id: string
  email: string
  role: string
  created_at?: string
}

export type Story = {
  id: string
  title: string | null
  status: string
  current_version_id: string | null
  created_at: string
}

export type Asset = {
  id: string
  kind: string
  line_id: string | null
  scene_id: string | null
  content_type: string
  duration_ms: number | null
  url: string
}

export type Version = {
  id: string
  story_id: string
  parent_version_id: string | null
  version_number: number
  genre: string | null
  mood: string | null
  created_at: string
}

export type Job = {
  stage: string
  status: string
  attempt: number
  error: string | null
  started_at: string | null
  finished_at: string | null
}

export type StageProgress = {
  stage: string
  completed: number
  total: number
}

export type Progress = {
  story_id: string
  version_id: string | null
  status: string
  /** Stages this run covers — a scoped regeneration only lists its own slice. */
  planned_stages?: string[]
  jobs: Job[]
  /** Per-line and per-scene completion for the fan-out stages, recomputed from
   *  stored assets. The live stream reports the same figures; this is what keeps the
   *  fan-out detailed when the stream is unavailable. */
  stage_progress?: StageProgress[]
}

export type RegenDirective = {
  scope?: string
  target_stage?: string
  target_id?: string | null
  instruction_delta?: string
  reason?: string
}

export type FeedbackEntry = {
  id: string
  raw_text: string
  status: string
  error: string | null
  directive_json: RegenDirective | null
  resulting_version_id: string | null
  created_at: string
}

export type StoryDetail = {
  story: Story
  version: Version | null
  state: StoryState | null
  assets: Asset[]
}

export type DispatchAccepted = {
  story_id: string
  version_id: string
  stages: string[]
  task_id: string
}

export type ExportFormat = {
  format: string
  label: string
  content_type: string
  /** Why you would pick this one. Written by the API so both apps agree. */
  detail: string
  /** The format the pipeline already produced, so downloading it is lossless. */
  recommended: boolean
  ready: boolean
  url: string | null
  size_bytes: number | null
}

export type IngestStatus = 'pending' | 'extracting' | 'cleaning' | 'ready' | 'failed'

export type Ingest = {
  id: string
  filename: string
  status: IngestStatus
  /** How the text came out: text_layer, ocr, docx or plain_text. */
  method: string | null
  page_count: number | null
  raw_chars: number | null
  cleaned_text: string | null
  title_hint: string | null
  genre_hint: string | null
  notes: string | null
  error: string | null
}

/** What the upload zone says while a file is being processed. Extraction gives
 *  no incremental progress, so these describe the phase rather than a percentage. */
export const INGEST_STATUS_LABELS: Record<IngestStatus, string> = {
  pending: 'Queued',
  extracting: 'Reading the file',
  cleaning: 'Cleaning up the text',
  ready: 'Ready to review',
  failed: 'Could not read that file',
}

export const INGEST_METHOD_LABELS: Record<string, string> = {
  text_layer: 'read directly',
  ocr: 'read with OCR',
  docx: 'read from Word',
  plain_text: 'read as text',
}

export type Scene = {
  id: string
  index: number
  title: string
  summary: string
  setting: string
  mood_tag: string
}

export type Character = {
  id: string
  name: string
  role: string
  personality: string
  sample_line: string
  voice_preset?: string | null
}

export type DialogueLine = {
  id: string
  scene_id: string
  index: number
  speaker: string
  character_id?: string | null
  text: string
  line_type: string
  emotion?: string | null
  intensity?: number | null
  tts_instructions?: string | null
  pause_after_ms?: number
}

export type StoryState = {
  story_id: string
  version_id: string
  user_id: string
  raw_text: string
  genre_hint?: string | null
  mood?: {
    genre: string
    mood: string
    tone_keywords: string[]
    pacing: string
  } | null
  title?: string | null
  arc_summary?: string | null
  setting?: string | null
  scenes: Scene[]
  characters: Character[]
  lines: DialogueLine[]
  narrator_persona?: {
    persona_name: string
    tone: string
    pacing: string
    style_notes: string
    delivery_template: string
  } | null
  output_format?: string
  final_episode_key?: string | null
  final_video_key?: string | null
}

/** Panels of the studio. `editor` is the video editor, which is deliberately a
 *  sibling of the generation view rather than part of it: nothing in it runs the
 *  pipeline, and it should not be on screen while one is still finishing. */
export type StudioTab = 'episode' | 'scenes' | 'editor'

export type View =
  | { name: 'landing' }
  | { name: 'library' }
  | { name: 'compose' }
  | { name: 'studio'; storyId: string; tab: StudioTab }
  /** A shared cut. The only view that renders without a session. */
  | { name: 'share'; token: string }

// --- video editor ----------------------------------------------------------

export type AspectRatio = '16:9' | '9:16' | '1:1' | '4:5'
export type FrameFill = 'crop' | 'blur'
export type CaptionFont = 'sans' | 'serif' | 'mono' | 'noto_sans' | 'noto_serif'
export type CaptionPosition = 'top' | 'middle' | 'bottom'
export type Corner = 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'
export type RenderStatus = 'draft' | 'queued' | 'rendering' | 'ready' | 'failed'

export type CaptionStyle = {
  enabled: boolean
  font: CaptionFont
  size_pt: number
  primary_color: string
  outline_color: string
  outline_px: number
  shadow_px: number
  box: boolean
  box_opacity: number
  position: CaptionPosition
  margin_px: number
  bold: boolean
  italic: boolean
  uppercase: boolean
  max_chars_per_line: number
  show_speaker: boolean
}

export type TrimRange = {
  start_ms: number
  /** Null means "to the end", which is what a new cut holds. */
  end_ms: number | null
}

export type AudioMix = {
  narration_gain_db: number
  keep_score: boolean
  score_gain_db: number
  local_asset_id: string | null
  local_gain_db: number
  /** Positive delays the track; negative starts it partway in. */
  local_offset_ms: number
  local_loop: boolean
  duck_under_narration: boolean
  fade_in_ms: number
  fade_out_ms: number
}

export type TitleCard = {
  enabled: boolean
  heading: string
  subheading: string
  duration_ms: number
}

export type Watermark = {
  enabled: boolean
  text: string
  position: Corner
  opacity: number
}

export type Motion = {
  ken_burns: boolean
  transition_ms: number
}

export type VideoEditManifest = {
  aspect: AspectRatio
  frame_fill: FrameFill
  trim: TrimRange
  caption: CaptionStyle
  caption_overrides: Record<string, string>
  audio: AudioMix
  title_card: TitleCard
  watermark: Watermark
  motion: Motion
}

/** One caption, timed against the untrimmed episode. The preview overlay reads
 *  these so the browser is working from the timeline the renderer will use
 *  rather than adding durations up itself. */
export type CaptionCue = {
  line_id: string
  scene_id: string
  index: number
  start_ms: number
  /** Where the picture changes: the spoken part plus its trailing pause. */
  end_ms: number
  /** Where the caption clears: the spoken part only. */
  caption_end_ms: number
  speaker: string
  text: string
  image_url: string | null
}

export type VideoEdit = {
  id: string
  story_id: string
  version_id: string
  name: string
  manifest: VideoEditManifest
  status: RenderStatus
  error: string | null
  video_url: string | null
  download_url: string | null
  size_bytes: number | null
  duration_ms: number | null
  /** False once the manifest has moved on from what was rendered. */
  render_current: boolean
  /** False when the story was regenerated after this cut was made. */
  version_current: boolean
  share_url: string | null
  share_expires_at: string | null
  share_views: number
  created_at: string
  updated_at: string
}

export type EditorCapabilities = {
  has_score: boolean
  has_artwork: boolean
  line_count: number
  duration_ms: number
}

export type CaptionPreset = {
  key: string
  label: string
  detail: string
  style: CaptionStyle
}

export type EditorOption = {
  key: string
  label: string
  detail: string
}

export type AspectOption = EditorOption & {
  width: number
  height: number
}

export type LocalAudio = {
  id: string
  filename: string
  content_type: string
  duration_ms: number | null
  size_bytes: number | null
  url: string
}

export type VideoEditorBootstrap = {
  story_id: string
  version_id: string
  title: string | null
  capabilities: EditorCapabilities
  cues: CaptionCue[]
  source_video_url: string | null
  episode_audio_url: string | null
  edits: VideoEdit[]
  local_audio: LocalAudio[]
  caption_presets: CaptionPreset[]
  fonts: EditorOption[]
  aspects: AspectOption[]
}

export type ShareInfo = {
  share_url: string
  expires_at: string | null
}

export type SharedCut = {
  title: string | null
  name: string
  duration_ms: number | null
  aspect: string
  video_url: string
  expires_at: string | null
}

export const RENDER_STATUS_LABELS: Record<RenderStatus, string> = {
  draft: 'Not exported yet',
  queued: 'Queued',
  rendering: 'Rendering',
  ready: 'Exported',
  failed: 'Export failed',
}

export const STAGE_LABELS: Record<string, string> = {
  mood_classification: 'Mood',
  story_understanding: 'Scenes',
  character_registry: 'Cast',
  dialogue_attribution: 'Script',
  emotion_tagging: 'Emotion',
  narrator_persona: 'Narrator',
  voice_assignment: 'Voices',
  tts_synthesis: 'Speech',
  image_generation: 'Imagery',
  music_generation: 'Score',
  assembly: 'Mix',
  video_composition: 'Video',
}

/** What each stage actually does, written for a listener rather than an engineer. */
export const STAGE_DESCRIPTIONS: Record<string, string> = {
  mood_classification:
    'Reads your story and decides its genre, tone and pace, which every later stage takes its cues from.',
  story_understanding:
    'Breaks the story into scenes and writes the arc, setting and title.',
  character_registry:
    'Works out who is in the story and gives each person an age, gender and personality.',
  dialogue_attribution:
    'Turns the prose into a script, splitting it into narration and spoken lines and assigning each line to a character.',
  emotion_tagging:
    'Marks every line with an emotion and intensity, so the voice knows whether to whisper it or shout it.',
  narrator_persona:
    'Chooses how the narrator should sound: their warmth, pace and distance from the story.',
  voice_assignment:
    'Casts a distinct voice for each character, matched to their age, gender and personality. No two characters share one.',
  tts_synthesis:
    'Records every line with its assigned voice and emotion. This is the longest stage.',
  image_generation: 'Paints cover artwork for each scene.',
  music_generation:
    'Writes one subtle instrumental bed beneath the narration. If the private music Mac is offline, the episode still finishes without it.',
  assembly:
    'Stitches the recorded lines together with pacing and pauses into the final episode.',
  video_composition:
    'Combines scene artwork with the audio mix into a finished video.',
}

export const PIPELINE_ORDER = Object.keys(STAGE_LABELS)
