import { waveform } from '../services/studio'

interface Props { bars?: number; seed?: number; active?: boolean; progress?: number; height?: number; color?: string; onSeek?: (pct: number) => void }
export function WaveformSpine({ bars = 96, seed = 7, active = false, progress, height = 56, color = 'var(--ember)', onSeek }: Props) {
  return <div className={`waveform-spine ${active ? 'is-active' : ''}`} style={{ height }} role={onSeek ? 'slider' : 'img'} aria-label="Audio waveform" aria-valuenow={progress == null ? undefined : Math.round(progress * 100)} onClick={(event) => { if (!onSeek) return; const rect = event.currentTarget.getBoundingClientRect(); onSeek(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))) }}>
    {waveform(bars, seed).map((value, index) => <i key={index} style={{ height: `${value * 100}%`, background: progress == null || index / bars <= progress ? color : `color-mix(in oklab, ${color} 28%, transparent)` }} />)}
  </div>
}
