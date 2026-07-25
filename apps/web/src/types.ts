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

export type Progress = {
  story_id: string
  version_id: string | null
  status: string
  /** Stages this run covers — a scoped regeneration only lists its own slice. */
  planned_stages?: string[]
  jobs: Job[]
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
  final_episode_key?: string | null
}

export type View =
  | { name: 'landing' }
  | { name: 'library' }
  | { name: 'compose' }
  | { name: 'studio'; storyId: string }

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
  assembly: 'Mix',
}

export const PIPELINE_ORDER = Object.keys(STAGE_LABELS)
