import type { Asset, DialogueLine, Scene } from '../types'

type Props = {
  scenes: Scene[]
  images: Asset[]
  lines: DialogueLine[]
  /** Artwork arrives well after the scene list does. */
  pending: boolean
  onPlayScene?: (lineId: string) => void
}

export function ScenePanel({ scenes, images, lines, pending, onPlayScene }: Props) {
  if (!scenes.length) {
    return (
      <section className="scenes-empty">
        <p className="muted">
          {pending
            ? 'Blocking out the scenes. Artwork follows once the script is set.'
            : 'This episode has no scenes yet.'}
        </p>
      </section>
    )
  }

  return (
    <div className="scene-gallery">
      {scenes.map((scene) => {
        const image = images.find((a) => a.scene_id === scene.id)
        const sceneLines = lines.filter((l) => l.scene_id === scene.id)
        const firstLine = sceneLines[0]
        return (
          <article key={scene.id} className="scene-card">
            <div className="scene-art">
              {image ? (
                <img src={image.url} alt={`Artwork for ${scene.title}`} loading="lazy" />
              ) : (
                <div className="scene-art-placeholder">
                  <span>{pending ? 'Painting…' : 'No artwork'}</span>
                </div>
              )}
            </div>
            <div className="scene-body">
              <p className="eyebrow">Scene {scene.index + 1}</p>
              <h3>{scene.title}</h3>
              <p className="scene-summary">{scene.summary}</p>
              <dl className="scene-meta">
                <div>
                  <dt>Setting</dt>
                  <dd>{scene.setting || '—'}</dd>
                </div>
                <div>
                  <dt>Mood</dt>
                  <dd>{scene.mood_tag || '—'}</dd>
                </div>
                <div>
                  <dt>Lines</dt>
                  <dd>{sceneLines.length}</dd>
                </div>
              </dl>
              {firstLine && onPlayScene && (
                <button
                  type="button"
                  className="btn ghost small"
                  onClick={() => onPlayScene(firstLine.id)}
                >
                  Play from here
                </button>
              )}
            </div>
          </article>
        )
      })}
    </div>
  )
}
