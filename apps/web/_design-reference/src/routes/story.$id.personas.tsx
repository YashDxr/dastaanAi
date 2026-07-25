import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { PersonaCard } from "@/components/persona-card";
import { NARRATOR_PERSONAS, getStory } from "@/lib/mock-data";
import { WaveformSpine } from "@/components/waveform-spine";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/story/$id/personas")({
  head: () => ({
    meta: [
      { title: "Narrator personas · Daastaan AI" },
      { name: "description", content: "Compare narrator styles side by side on the same paragraph." },
      { property: "og:title", content: "Narrator personas" },
      { property: "og:description", content: "A/B the storyteller." },
    ],
  }),
  component: PersonasPage,
});

function PersonasPage() {
  const { id } = Route.useParams();
  const story = getStory(id);
  const [a, setA] = useState(NARRATOR_PERSONAS[0].id);
  const [b, setB] = useState(NARRATOR_PERSONAS[1].id);
  const pa = NARRATOR_PERSONAS.find((p) => p.id === a)!;
  const pb = NARRATOR_PERSONAS.find((p) => p.id === b)!;

  return (
    <div className="space-y-10">
      <div>
        <p className="text-[10px] uppercase tracking-[0.28em] text-[var(--ember)] mb-3">A/B Compare</p>
        <h2 className="font-display text-3xl md:text-4xl">Two narrators. One paragraph.</h2>
        <p className="mt-2 text-muted-foreground max-w-2xl">
          The story does not change. Only who tells it does. Pick two personas — hear them share a stage.
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {[{ p: pa, side: "A" }, { p: pb, side: "B" }].map(({ p, side }) => (
          <div key={side} className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
            <div className="flex items-center justify-between">
              <p className="text-[10px] uppercase tracking-widest text-muted-foreground">Side {side}</p>
              <span className="text-xs" style={{ color: p.color }}>{p.name}</span>
            </div>
            <p className="mt-4 font-display text-xl leading-relaxed">"{story.transcript[0].text} {story.transcript[1].text}"</p>
            <div className="mt-5">
              <WaveformSpine bars={90} seed={p.id.length + (side === "A" ? 1 : 4)} active height={48} color={p.color} />
            </div>
            <div className="mt-4 flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{p.vibe}</span>
              <Button size="sm" variant="outline" className="border-parchment/20 bg-transparent text-parchment hover:bg-white/5">Play</Button>
            </div>
          </div>
        ))}
      </div>

      <div>
        <p className="text-[10px] uppercase tracking-[0.28em] text-muted-foreground mb-4">All personas</p>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {NARRATOR_PERSONAS.map((p) => (
            <PersonaCard
              key={p.id}
              persona={p}
              selected={p.id === a}
              onSelect={() => (p.id === a ? setB(p.id) : setA(p.id))}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
