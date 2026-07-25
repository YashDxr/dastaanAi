export type Emotion = 'calm' | 'tense' | 'joy' | 'sorrow' | 'awe' | 'fear' | 'anger'
export type JobStatus = 'pending' | 'running' | 'completed' | 'failed'
export type EpisodeStatus = 'draft' | 'processing' | 'ready' | 'failed'
export interface Character { id: string; name: string; role: string; color: string; lines: number; voice: string; pitch: number; speed: number; energy: number; relationships: { to: string; label: string }[] }
export interface Scene { id: string; title: string; start: number; end: number; emotion: Emotion; summary: string }
export interface TranscriptLine { t: number; characterId: string | 'narrator'; text: string; emotion?: Emotion }
export interface Persona { id: string; name: string; vibe: string; description: string; color: string; sample: string }
export interface StoryDetail { id: string; title: string; author: string; genre: string; cover: string; duration: number; status: EpisodeStatus; updatedAt: string; persona: string; language: string; synopsis: string; characters: Character[]; scenes: Scene[]; transcript: TranscriptLine[]; processingProgress?: number }
export interface ProcessingStatus { storyId: string; progress: number; nodes: Record<string, JobStatus>; estimatedSeconds: number; logs: { level: 'info' | 'warn'; msg: string; node: string }[] }
export interface VoiceUpdate { characterId: string; pitch: number; speed: number; energy: number }
export interface StudioSettings { narrator: string; language: string; audioQuality: string; notifications: boolean; reducedMotion: boolean }

const characters: Character[] = [
  { id: 'ayla', name: 'Ayla', role: 'Protagonist', color: '#E8873A', lines: 128, voice: 'Ember-F 04', pitch: 2, speed: 1, energy: .7, relationships: [{ to: 'raza', label: 'sister' }, { to: 'kabir', label: 'loves' }] },
  { id: 'kabir', name: 'Kabir', role: 'Deuteragonist', color: '#4FD8C4', lines: 96, voice: 'Signal-M 02', pitch: -3, speed: .95, energy: .6, relationships: [{ to: 'ayla', label: 'loves' }] },
  { id: 'raza', name: 'Raza', role: 'Antagonist', color: '#E2504B', lines: 54, voice: 'Ash-M 07', pitch: -6, speed: .9, energy: .85, relationships: [{ to: 'ayla', label: 'sister' }] },
  { id: 'noor', name: 'Noor', role: 'Mentor', color: '#B57BFF', lines: 41, voice: 'Sage-F 01', pitch: -1, speed: .88, energy: .4, relationships: [{ to: 'ayla', label: 'guides' }] },
]
const scenes: Scene[] = ['The Empty House', 'Letters in the Attic', "Kabir's Confession", 'Raza Returns', 'The River at Dawn'].map((title, i) => ({ id: `s${i + 1}`, title, start: i * 360, end: (i + 1) * 360, emotion: (['tense','awe','joy','fear','calm'] as Emotion[])[i], summary: 'A pivotal movement in the story.' }))
const transcript: TranscriptLine[] = [{ t: 0, characterId: 'narrator', text: 'The door was already open when she got home.' }, { t: 4, characterId: 'narrator', text: "She hadn't left it that way." }, { t: 8, characterId: 'ayla', text: 'Kabir? Are you here?', emotion: 'tense' }, { t: 12, characterId: 'kabir', text: 'In the attic. You need to see this.', emotion: 'awe' }, { t: 23, characterId: 'ayla', text: 'Whose handwriting is this?', emotion: 'awe' }]
export const personas: Persona[] = [
  { id: 'thriller', name: 'The Thriller', vibe: 'Taut · Whispered · Cinematic', color: '#E2504B', description: 'Low register, tight breath, cinematic tension under every line.', sample: 'The door was already open when she got home.' },
  { id: 'romance', name: 'The Romantic', vibe: 'Warm · Intimate · Lingering', color: '#E8873A', description: 'Soft consonants and longer vowels, the pause between heartbeats.', sample: 'He looked at her the way old letters look at light.' },
  { id: 'fantasy', name: 'The Bard', vibe: 'Grand · Mythic · Sweeping', color: '#B57BFF', description: 'Full-throated pacing; every noun is a small kingdom.', sample: 'Beyond the seventh hill, the story began.' },
]
const mockStories: StoryDetail[] = [
  { id: 'st_1', title: "The Cartographer's Daughter", author: 'Meher Anand', genre: 'Literary Fiction', cover: 'linear-gradient(135deg,#E8873A,#8B3A1E 60%,#0E0B14)', duration: 1842, status: 'ready', updatedAt: '2h ago', persona: 'romance', language: 'English', synopsis: 'A woman inherits maps that lead to a place her father swore never existed.', characters, scenes, transcript },
  { id: 'st_2', title: 'Nights on Kolachi Street', author: 'Faraz Idris', genre: 'Thriller', cover: 'linear-gradient(135deg,#4FD8C4,#12574C 55%,#0E0B14)', duration: 2412, status: 'processing', updatedAt: 'just now', persona: 'thriller', language: 'English', synopsis: 'Three witnesses. One murder. Every story ends the same.', characters, scenes, transcript, processingProgress: 62 },
  { id: 'st_3', title: 'The Bird That Owned the Sky', author: 'Ila Rehman', genre: 'Fable', cover: 'linear-gradient(135deg,#B57BFF,#4A2A78 55%,#0E0B14)', duration: 1104, status: 'ready', updatedAt: 'yesterday', persona: 'thriller', language: 'Urdu', synopsis: 'A grandmother tells her grandchild the oldest family lie.', characters: characters.slice(0, 3), scenes, transcript },
]
const wait = <T,>(value: T) => new Promise<T>((resolve) => window.setTimeout(() => resolve(value), 180))
const generatedNodes: Record<string, JobStatus> = { understand: 'completed', registry: 'completed', dialogue: 'completed', emotion: 'completed', persona: 'completed', voice: 'running', music: 'running', assembly: 'pending', episode: 'pending' }

// TODO: replace with real fetch once POST /stories ships.
export const createStory = (input: { text: string; genre: string; persona: string; language: string }) => wait({ id: 'st_2', status: 'processing' as EpisodeStatus, ...input })
// TODO: replace with real fetch once GET /stories/{id} ships.
export const getStoryDetail = (id: string) => wait(mockStories.find((story) => story.id === id) ?? mockStories[0])
// TODO: replace with real fetch once GET /stories/{id}/processing ships.
export const getProcessingStatus = (storyId: string) => wait<ProcessingStatus>({ storyId, progress: 62, nodes: generatedNodes, estimatedSeconds: 126, logs: [{ level: 'info', msg: 'story.ingest ok · 4,812 tokens', node: 'understand' }, { level: 'info', msg: 'detected 4 characters', node: 'registry' }, { level: 'warn', msg: 'emotion confidence 0.61 · fallback: joy', node: 'emotion' }] })
// TODO: replace with real fetch once GET /stories/{id}/understanding ships.
export const getStoryUnderstanding = (id: string) => getStoryDetail(id)
// TODO: replace with real fetch once GET /stories/{id}/personas ships.
export const getNarratorPersonas = (_storyId: string) => wait(personas)
// TODO: replace with real fetch once GET /stories/{id}/voices ships.
export const getCharacterVoices = (id: string) => getStoryDetail(id).then((story) => story.characters)
// TODO: replace with real fetch once PATCH /stories/{id}/voices/{characterId} ships.
export const updateCharacterVoice = (_storyId: string, update: VoiceUpdate) => wait(update)
// TODO: replace with real fetch once GET /stories/{id}/episode ships.
export const getEpisodePlayer = (id: string) => getStoryDetail(id)
// TODO: replace with real fetch once GET /observability/jobs ships.
export const getObservability = () => wait({ activeJobs: 3, latency: '4m 12s', failureRate: '2.1%', logs: ['voice.gen ayla · 128 lines', 'music.gen scene s2', 'assembly · crossfades 34'] })
// TODO: replace with real fetch once GET/PATCH /settings ships.
export const getSettings = () => wait<StudioSettings>({ narrator: 'romance', language: 'English', audioQuality: 'Studio · 48kHz', notifications: true, reducedMotion: false })
// TODO: replace with real fetch once PATCH /settings ships.
export const saveSettings = (settings: StudioSettings) => wait(settings)
export const waveform = (n = 96, seed = 7) => Array.from({ length: n }, (_, i) => { const x = Math.sin((i + seed) * 12.9898) * 43758.5453; return .2 + .8 * Math.abs(x - Math.floor(x)) })
