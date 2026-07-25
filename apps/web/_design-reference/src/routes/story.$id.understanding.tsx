import { createFileRoute } from "@tanstack/react-router";
import { getStory } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/story/$id/understanding")({
  head: () => ({
    meta: [
      { title: "Story understanding · Daastaan AI" },
      { name: "description", content: "Scene timeline, character relationships and emotion map for your story." },
      { property: "og:title", content: "Story understanding" },
      { property: "og:description", content: "How Daastaan reads your story." },
    ],
  }),
  component: UnderstandingPage,
});

const EMOTION_COLOR: Record<string, string> = {
  calm: "#4FD8C4", tense: "#E2504B", joy: "#F3D48A", sorrow: "#8FB8FF", awe: "#B57BFF", fear: "#E2504B", anger: "#E8873A",
};

function UnderstandingPage() {
  const { id } = Route.useParams();
  const story = getStory(id);

  const totalLines = story.characters.reduce((a, c) => a + c.lines, 0);

  return (
    <div className="space-y-10">
      {/* scene timeline */}
      <section>
        <p className="text-[10px] uppercase tracking-[0.24em] text-muted-foreground mb-3">Scene timeline</p>
        <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
          <div className="grid gap-4">
            {story.scenes.map((sc, i) => (
              <div key={sc.id} className="grid grid-cols-[80px_minmax(0,1fr)_140px] items-center gap-4">
                <span className="font-mono text-xs text-muted-foreground">Scene {i + 1}</span>
                <div>
                  <p className="font-display text-lg">{sc.title}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">{sc.summary}</p>
                </div>
                <span
                  className="justify-self-end text-[10px] uppercase tracking-widest px-2 py-1 rounded-full"
                  style={{ background: `${EMOTION_COLOR[sc.emotion]}22`, color: EMOTION_COLOR[sc.emotion] }}
                >
                  {sc.emotion}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="grid gap-6 lg:grid-cols-2">
        {/* Dialogue distribution */}
        <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
          <p className="text-[10px] uppercase tracking-[0.24em] text-muted-foreground mb-4">Dialogue distribution</p>
          <div className="flex h-4 rounded-full overflow-hidden">
            {story.characters.map((c) => (
              <div key={c.id} style={{ width: `${(c.lines / totalLines) * 100}%`, background: c.color }} title={c.name} />
            ))}
          </div>
          <div className="mt-4 space-y-2">
            {story.characters.map((c) => (
              <div key={c.id} className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full" style={{ background: c.color }} />
                  <span>{c.name}</span>
                </div>
                <span className="font-mono text-xs text-muted-foreground">{c.lines} · {Math.round((c.lines / totalLines) * 100)}%</span>
              </div>
            ))}
          </div>
        </div>

        {/* Emotion timeline */}
        <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
          <p className="text-[10px] uppercase tracking-[0.24em] text-muted-foreground mb-4">Emotion arc</p>
          <div className="grid grid-cols-5 gap-2 h-32">
            {story.scenes.map((sc) => {
              const h = { calm: 30, tense: 80, joy: 60, sorrow: 55, awe: 70, fear: 85, anger: 90 }[sc.emotion] ?? 50;
              return (
                <div key={sc.id} className="flex flex-col justify-end">
                  <div className="rounded-t-md" style={{ height: `${h}%`, background: EMOTION_COLOR[sc.emotion] }} />
                  <p className="text-[10px] text-center mt-1 text-muted-foreground truncate">{sc.emotion}</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      {/* Character cards + relationship graph */}
      <section>
        <p className="text-[10px] uppercase tracking-[0.24em] text-muted-foreground mb-4">Characters & relationships</p>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {story.characters.map((c) => (
            <div key={c.id} className={cn("rounded-2xl border border-border bg-[var(--ink-2)]/60 p-5")}>
              <div className="flex items-center gap-3">
                <span className="grid h-10 w-10 place-items-center rounded-full font-display" style={{ background: `${c.color}25`, color: c.color }}>
                  {c.name[0]}
                </span>
                <div>
                  <p className="font-display text-lg">{c.name}</p>
                  <p className="text-[11px] text-muted-foreground">{c.role}</p>
                </div>
              </div>
              <p className="text-xs mt-3 text-muted-foreground">Voice · <span className="font-mono text-parchment/80">{c.voice}</span></p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {c.relationships.map((r) => {
                  const other = story.characters.find((x) => x.id === r.to);
                  if (!other) return null;
                  return (
                    <span key={r.to} className="text-[10px] px-2 py-0.5 rounded-full border border-border">
                      <span className="text-muted-foreground">{r.label} → </span>
                      <span style={{ color: other.color }}>{other.name}</span>
                    </span>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
