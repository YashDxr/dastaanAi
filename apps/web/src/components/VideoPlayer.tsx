import { useMemo, useRef } from 'react'
import type { Asset } from '../types'

type Props = {
  asset: Asset
  title?: string | null
  regenerating?: boolean
}

export function VideoPlayer({ asset, title, regenerating = false }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const videoUrl = useMemo(() => asset.url, [asset.url])

  return (
    <section className="player video-player">
      <p className="eyebrow">
        {regenerating ? 'Previous video (rebuild in progress)' : 'Video'}
      </p>
      <h2>{title || 'Untitled episode'}</h2>
      <video
        ref={videoRef}
        src={videoUrl}
        controls
        preload="metadata"
        className="video-element"
      />
    </section>
  )
}

export function VideoPlayerEmpty({ regenerating = false }: { regenerating?: boolean }) {
  return (
    <section className="player empty">
      <p className="eyebrow">Video</p>
      <h2>{regenerating ? 'Composing video…' : 'Waiting for video'}</h2>
      <p className="muted">
        {regenerating
          ? 'Scene artwork and audio are being combined. The video will appear when ready.'
          : 'Video appears here once composition finishes.'}
      </p>
    </section>
  )
}
