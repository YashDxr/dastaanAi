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
  | { name: 'mystery-compose' }
  | { name: 'mystery'; storyId: string }

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
    'Casts a real voice for each character, matching age, gender and personality.',
  tts_synthesis:
    'Records every line with its assigned voice and emotion. This is the longest stage.',
  image_generation: 'Paints cover artwork for each scene.',
  assembly:
    'Stitches the recorded lines together with pacing and pauses into the final episode.',
}

export const PIPELINE_ORDER = Object.keys(STAGE_LABELS)
