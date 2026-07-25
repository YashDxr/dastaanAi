export type EmotionTag = "calm" | "tense" | "joy" | "sorrow" | "awe" | "fear" | "anger";
export type NodeStatus = "pending" | "running" | "completed" | "failed";
export type EpisodeStatus = "processing" | "ready" | "draft" | "failed";

export interface Character {
  id: string;
  name: string;
  role: string;
  color: string;
  lines: number;
  voice: string;
  pitch: number; // -12..12
  speed: number; // 0.5..1.5
  energy: number; // 0..1
  relationships: { to: string; label: string }[];
}

export interface Scene {
  id: string;
  title: string;
  start: number; // seconds
  end: number;
  emotion: EmotionTag;
  summary: string;
}

export interface TranscriptLine {
  t: number;
  characterId: string | "narrator";
  text: string;
  emotion?: EmotionTag;
}

export interface NarratorPersona {
  id: string;
  name: string;
  vibe: string;
  description: string;
  color: string;
  sample: string;
}

export interface Story {
  id: string;
  title: string;
  author: string;
  genre: string;
  cover: string; // gradient css
  duration: number; // seconds
  status: EpisodeStatus;
  updatedAt: string;
  persona: string;
  language: string;
  synopsis: string;
  characters: Character[];
  scenes: Scene[];
  transcript: TranscriptLine[];
  processingProgress?: number;
}

export const NARRATOR_PERSONAS: NarratorPersona[] = [
  { id: "thriller", name: "The Thriller", vibe: "Taut · Whispered · Cinematic", color: "#E2504B", description: "Low register, tight breath, cinematic tension threaded under every line.", sample: "The door was already open when she got home. She hadn't left it that way." },
  { id: "romance", name: "The Romantic", vibe: "Warm · Intimate · Lingering", color: "#E8873A", description: "Soft consonants, longer vowels, the pause between two heartbeats.", sample: "He looked at her the way old letters look at the light — carefully, and for a long time." },
  { id: "fantasy", name: "The Bard", vibe: "Grand · Mythic · Sweeping", color: "#B57BFF", description: "Full-throated, orchestral pacing, every noun a small kingdom.", sample: "Beyond the seventh hill, where the map ended and the story began, she drew her blade." },
  { id: "podcast", name: "Podcast Host", vibe: "Casual · Modern · Curious", color: "#4FD8C4", description: "Conversational, mid-pace, leans in for the twist.", sample: "So — and this is the part nobody talks about — the letter wasn't from her father at all." },
  { id: "grandmother", name: "Grandmother", vibe: "Tender · Slow · Familiar", color: "#F3D48A", description: "Rocking cadence, small chuckles, the fire in the next room.", sample: "Now sit close, bacha. This story, my mother told me, and hers told her." },
  { id: "sage", name: "Ancient Sage", vibe: "Timeless · Ritual · Deep", color: "#8FB8FF", description: "Ceremonial pace, deliberate breath, a voice older than the paper.", sample: "In the age before names, when rivers still remembered their gods, a girl was born under an eclipse." },
];

const CHARS = (): Character[] => [
  { id: "ayla", name: "Ayla", role: "Protagonist", color: "#E8873A", lines: 128, voice: "Ember-F 04", pitch: 2, speed: 1, energy: 0.7, relationships: [{ to: "raza", label: "sister" }, { to: "kabir", label: "loves" }] },
  { id: "kabir", name: "Kabir", role: "Deuteragonist", color: "#4FD8C4", lines: 96, voice: "Signal-M 02", pitch: -3, speed: 0.95, energy: 0.6, relationships: [{ to: "ayla", label: "loves" }] },
  { id: "raza", name: "Raza", role: "Antagonist", color: "#E2504B", lines: 54, voice: "Ash-M 07", pitch: -6, speed: 0.9, energy: 0.85, relationships: [{ to: "ayla", label: "sister" }] },
  { id: "noor", name: "Noor", role: "Mentor", color: "#B57BFF", lines: 41, voice: "Sage-F 01", pitch: -1, speed: 0.88, energy: 0.4, relationships: [{ to: "ayla", label: "guides" }] },
];

const SCENES = (dur: number): Scene[] => [
  { id: "s1", title: "The Empty House", start: 0, end: dur * 0.18, emotion: "tense", summary: "Ayla returns to find the door ajar." },
  { id: "s2", title: "Letters in the Attic", start: dur * 0.18, end: dur * 0.42, emotion: "awe", summary: "A trunk of unsent letters is discovered." },
  { id: "s3", title: "Kabir's Confession", start: dur * 0.42, end: dur * 0.63, emotion: "joy", summary: "An old promise, finally spoken aloud." },
  { id: "s4", title: "Raza Returns", start: dur * 0.63, end: dur * 0.85, emotion: "fear", summary: "The brother thought lost walks back in." },
  { id: "s5", title: "The River at Dawn", start: dur * 0.85, end: dur, emotion: "calm", summary: "Three siblings, one river, one decision." },
];

const TRANSCRIPT: TranscriptLine[] = [
  { t: 0, characterId: "narrator", text: "The door was already open when she got home." },
  { t: 4, characterId: "narrator", text: "She hadn't left it that way." },
  { t: 8, characterId: "ayla", text: "Kabir? Are you here?", emotion: "tense" },
  { t: 12, characterId: "kabir", text: "In the attic. You need to see this.", emotion: "awe" },
  { t: 17, characterId: "narrator", text: "The stairs creaked the way old houses remember footsteps." },
  { t: 23, characterId: "ayla", text: "Whose handwriting is this?", emotion: "awe" },
  { t: 27, characterId: "kabir", text: "Yours. From a year you don't remember.", emotion: "sorrow" },
  { t: 33, characterId: "raza", text: "So you found them. I hoped you wouldn't.", emotion: "anger" },
  { t: 39, characterId: "narrator", text: "Outside, the river began its slow argument with the dawn." },
];

export const STORIES: Story[] = [
  {
    id: "st_1",
    title: "The Cartographer's Daughter",
    author: "Meher Anand",
    genre: "Literary Fiction",
    cover: "linear-gradient(135deg,#E8873A 0%,#8B3A1E 60%,#0E0B14 100%)",
    duration: 1842,
    status: "ready",
    updatedAt: "2h ago",
    persona: "romance",
    language: "English",
    synopsis: "A woman inherits her father's maps and discovers that one of them leads to a place he swore never existed.",
    characters: CHARS(),
    scenes: SCENES(1842),
    transcript: TRANSCRIPT,
  },
  {
    id: "st_2",
    title: "Nights on Kolachi Street",
    author: "Faraz Idris",
    genre: "Thriller",
    cover: "linear-gradient(135deg,#4FD8C4 0%,#12574C 55%,#0E0B14 100%)",
    duration: 2412,
    status: "processing",
    updatedAt: "just now",
    persona: "thriller",
    language: "English",
    synopsis: "Three witnesses. One murder. Every story ends the same, and none of them are true.",
    characters: CHARS(),
    scenes: SCENES(2412),
    transcript: TRANSCRIPT,
    processingProgress: 62,
  },
  {
    id: "st_3",
    title: "The Bird That Owned the Sky",
    author: "Ila Rehman",
    genre: "Fable",
    cover: "linear-gradient(135deg,#B57BFF 0%,#4A2A78 55%,#0E0B14 100%)",
    duration: 1104,
    status: "ready",
    updatedAt: "yesterday",
    persona: "grandmother",
    language: "Urdu",
    synopsis: "A grandmother tells her grandchild the oldest lie in the family, and the truth folded inside it.",
    characters: CHARS().slice(0, 3),
    scenes: SCENES(1104),
    transcript: TRANSCRIPT,
  },
  {
    id: "st_4",
    title: "Ember, Before the War",
    author: "Nadir Qadri",
    genre: "Fantasy",
    cover: "linear-gradient(135deg,#F3D48A 0%,#8A6A20 55%,#0E0B14 100%)",
    duration: 3241,
    status: "draft",
    updatedAt: "3d ago",
    persona: "fantasy",
    language: "English",
    synopsis: "In the year the sun was arrested, a blacksmith's daughter is drafted to hammer light back into the sky.",
    characters: CHARS(),
    scenes: SCENES(3241),
    transcript: TRANSCRIPT,
  },
  {
    id: "st_5",
    title: "Letters to a River",
    author: "Sana Kaif",
    genre: "Romance",
    cover: "linear-gradient(135deg,#E8873A 0%,#B57BFF 100%)",
    duration: 1520,
    status: "ready",
    updatedAt: "5d ago",
    persona: "podcast",
    language: "English",
    synopsis: "Two lovers write to a river for a decade before they write to each other.",
    characters: CHARS().slice(0, 2),
    scenes: SCENES(1520),
    transcript: TRANSCRIPT,
  },
];

export const getStory = (id: string) => STORIES.find((s) => s.id === id) ?? STORIES[0];

export const PIPELINE_NODES = [
  { id: "understand", label: "Story Understanding", group: 0 },
  { id: "registry", label: "Character Registry", group: 1 },
  { id: "dialogue", label: "Dialogue Split", group: 1 },
  { id: "emotion", label: "Emotion Detection", group: 2 },
  { id: "persona", label: "Narrator Persona", group: 2 },
  { id: "voice", label: "Voice Generation", group: 3 },
  { id: "music", label: "Music Generation", group: 3 },
  { id: "assembly", label: "Assembly", group: 4 },
  { id: "episode", label: "Episode", group: 5 },
] as const;

export const PIPELINE_EDGES: [string, string][] = [
  ["understand", "registry"],
  ["understand", "dialogue"],
  ["registry", "emotion"],
  ["dialogue", "emotion"],
  ["emotion", "persona"],
  ["persona", "voice"],
  ["persona", "music"],
  ["voice", "assembly"],
  ["music", "assembly"],
  ["assembly", "episode"],
];

export const LIVE_LOG_LINES = [
  { level: "info", msg: "story.ingest ok · 4,812 tokens", node: "understand" },
  { level: "info", msg: "detected 4 characters: Ayla, Kabir, Raza, Noor", node: "registry" },
  { level: "info", msg: "dialogue split · 128 lines · 41 narration blocks", node: "dialogue" },
  { level: "warn", msg: "emotion(scene s3) low confidence 0.61 · fallback: 'joy'", node: "emotion" },
  { level: "info", msg: "narrator.persona=romance · warmth 0.82", node: "persona" },
  { level: "info", msg: "voice.gen ayla · 128 lines · 42.1s", node: "voice" },
  { level: "info", msg: "music.gen scene s2 · 'attic, discovery, hush'", node: "music" },
  { level: "info", msg: "assembly · crossfades 34 · ducking -6dB", node: "assembly" },
  { level: "info", msg: "episode.ready · 30m 42s", node: "episode" },
];

// deterministic pseudo-waveform (values quantized to 4 decimals so SSR and client stringify identically)
export const waveform = (n = 96, seed = 7) => {
  const out: number[] = [];
  let s = seed;
  for (let i = 0; i < n; i++) {
    s = (s * 9301 + 49297) % 233280;
    const r = s / 233280;
    const env = Math.sin((i / n) * Math.PI);
    out.push(Math.round((0.15 + 0.85 * (0.4 + 0.6 * r) * (0.4 + 0.6 * env)) * 10000) / 10000);
  }
  return out;
};
