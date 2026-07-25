import type { StoryDetail } from '../services/studio'

const duration = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
export function EpisodeCard({ story, go }: { story: StoryDetail; go: (to: string) => void }) { return <button className="episode-card" onClick={() => go(story.status === 'processing' ? `/processing/${story.id}` : `/story/${story.id}/player`)}><div style={{ background: story.cover }}><span className="status"><i />{story.status}</span></div><small>{story.genre} · {duration(story.duration)}</small><strong>{story.title}</strong><em>{story.updatedAt}</em></button> }
