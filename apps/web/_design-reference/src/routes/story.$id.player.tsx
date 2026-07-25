import { createFileRoute } from "@tanstack/react-router";
import { PlayerControls } from "@/components/player-controls";
import { getStory } from "@/lib/mock-data";
import { WaveformSpine } from "@/components/waveform-spine";

export const Route = createFileRoute("/story/$id/player")({
  head: () => ({
    meta: [
      { title: "Episode · Daastaan AI" },
      { name: "description", content: "Listen to the cinematic episode with waveform, transcript sync and scene markers." },
      { property: "og:title", content: "Episode player" },
      { property: "og:description", content: "Story-first playback." },
    ],
  }),
  component: PlayerPage,
});

function PlayerPage() {
  const { id } = Route.useParams();
  const story = getStory(id);

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-6">
        {/* Cover */}
        <div className="relative overflow-hidden rounded-3xl border border-border aspect-[21/9]" style={{ background: story.cover }}>
          <div className="absolute inset-0 bg-gradient-to-t from-[var(--ink)]/95 via-[var(--ink)]/30 to-transparent" />
          <div className="absolute inset-0 p-8 flex flex-col justify-end">
            <p className="text-[10px] uppercase tracking-[0.24em] text-parchment/70">Episode 01 · {story.genre}</p>
            <h2 className="font-display text-3xl md:text-5xl max-w-3xl mt-2">{story.title}</h2>
            <p className="mt-2 text-parchment/80 max-w-2xl">{story.synopsis}</p>
          </div>
        </div>

        <PlayerControls story={story} />

        {/* Scene timeline */}
        <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
          <p className="text-[10px] uppercase tracking-widest text-muted-foreground mb-4">Scenes</p>
          <div className="relative h-14 rounded-lg overflow-hidden bg-white/[0.03] flex">
            {story.scenes.map((sc, i) => {
              const width = ((sc.end - sc.start) / story.duration) * 100;
              const colors = ["#E8873A", "#4FD8C4", "#B57BFF", "#E2504B", "#F3D48A"];
              return (
                <div
                  key={sc.id}
                  className="relative border-r border-[var(--ink)]/50 flex items-center justify-center text-[10px] uppercase tracking-widest text-[var(--ink)]"
                  style={{ width: `${width}%`, background: colors[i % colors.length] }}
                  title={sc.title}
                >
                  <span className="truncate px-2 opacity-80">{sc.title}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Transcript */}
      <aside className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6 h-fit lg:sticky lg:top-24">
        <div className="flex items-center justify-between mb-4">
          <p className="text-[10px] uppercase tracking-widest text-muted-foreground">Transcript</p>
          <WaveformSpine bars={20} seed={2} active height={16} color="var(--signal)" />
        </div>
        <div className="space-y-4 max-h-[560px] overflow-auto pr-1">
          {story.transcript.map((line, i) => {
            const char = story.characters.find((c) => c.id === line.characterId);
            const isNarrator = line.characterId === "narrator";
            return (
              <div key={i} className="text-sm">
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className="text-[10px] uppercase tracking-widest px-2 py-0.5 rounded-full"
                    style={{
                      background: isNarrator ? "rgba(243,234,216,0.08)" : `${char?.color}25`,
                      color: isNarrator ? "var(--muted-foreground)" : char?.color,
                    }}
                  >
                    {isNarrator ? "Narrator" : char?.name}
                  </span>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {Math.floor(line.t / 60)}:{String(line.t % 60).padStart(2, "0")}
                  </span>
                </div>
                <p className="text-parchment/90 leading-relaxed">{line.text}</p>
              </div>
            );
          })}
        </div>
      </aside>
    </div>
  );
}
