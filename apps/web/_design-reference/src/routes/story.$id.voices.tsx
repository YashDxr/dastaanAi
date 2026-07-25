import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { getStory } from "@/lib/mock-data";
import { Slider } from "@/components/ui/slider";
import { WaveformSpine } from "@/components/waveform-spine";
import { Button } from "@/components/ui/button";
import { Play, Wand2 } from "lucide-react";

export const Route = createFileRoute("/story/$id/voices")({
  head: () => ({
    meta: [
      { title: "Character voices · Daastaan AI" },
      { name: "description", content: "Cast every character. Tune pitch, speed and energy per voice." },
      { property: "og:title", content: "Character voices" },
      { property: "og:description", content: "The voice studio." },
    ],
  }),
  component: VoicesPage,
});

function VoicesPage() {
  const { id } = Route.useParams();
  const story = getStory(id);
  const [activeId, setActiveId] = useState(story.characters[0].id);
  const active = story.characters.find((c) => c.id === activeId)!;
  const [pitch, setPitch] = useState([active.pitch]);
  const [speed, setSpeed] = useState([active.speed * 100]);
  const [energy, setEnergy] = useState([active.energy * 100]);

  const onSelect = (cid: string) => {
    const c = story.characters.find((x) => x.id === cid)!;
    setActiveId(cid);
    setPitch([c.pitch]);
    setSpeed([c.speed * 100]);
    setEnergy([c.energy * 100]);
  };

  return (
    <div className="grid gap-8 lg:grid-cols-[280px_minmax(0,1fr)]">
      <div className="space-y-2">
        <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground mb-3">Cast</p>
        {story.characters.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`w-full text-left rounded-xl border p-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ember)]/60 ${
              activeId === c.id ? "border-[var(--ember)]/60 bg-[var(--ember)]/8" : "border-border bg-[var(--ink-2)]/40 hover:border-parchment/25"
            }`}
          >
            <div className="flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-full font-display" style={{ background: `${c.color}25`, color: c.color }}>
                {c.name[0]}
              </span>
              <div className="min-w-0">
                <p className="text-sm truncate">{c.name}</p>
                <p className="text-[11px] text-muted-foreground truncate">{c.role} · {c.lines} lines</p>
              </div>
            </div>
          </button>
        ))}
      </div>

      <div className="space-y-6">
        <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-[10px] uppercase tracking-widest text-muted-foreground">Voice preview</p>
              <h3 className="font-display text-3xl mt-1">{active.name}</h3>
              <p className="text-xs text-muted-foreground mt-1">Voice model · <span className="font-mono">{active.voice}</span></p>
            </div>
            <Button className="bg-[var(--ember)] text-[var(--ink)] hover:bg-[var(--ember)]/90 ember-glow">
              <Play className="h-4 w-4 mr-2" /> Preview
            </Button>
          </div>
          <div className="mt-6">
            <WaveformSpine bars={120} seed={active.name.length * 5} active height={64} color={active.color} />
          </div>
        </div>

        <div className="grid gap-6 md:grid-cols-3">
          <Control label="Pitch" value={`${pitch[0]}`} suffix="st"><Slider value={pitch} onValueChange={setPitch} min={-12} max={12} step={1} /></Control>
          <Control label="Speed" value={`${(speed[0] / 100).toFixed(2)}`} suffix="x"><Slider value={speed} onValueChange={setSpeed} min={50} max={150} step={1} /></Control>
          <Control label="Energy" value={`${energy[0]}`} suffix="%"><Slider value={energy} onValueChange={setEnergy} min={0} max={100} step={1} /></Control>
        </div>

        <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
          <p className="text-[10px] uppercase tracking-widest text-muted-foreground mb-3">Relationships</p>
          <div className="flex flex-wrap gap-2">
            {active.relationships.map((r) => {
              const other = story.characters.find((c) => c.id === r.to);
              if (!other) return null;
              return (
                <span key={r.to} className="text-xs px-3 py-1.5 rounded-full border border-border bg-[var(--ink-3)]/60">
                  <span className="text-muted-foreground">{r.label} → </span>
                  <span style={{ color: other.color }}>{other.name}</span>
                </span>
              );
            })}
          </div>
        </div>

        <div className="flex justify-end">
          <Button className="bg-[var(--ember)] text-[var(--ink)] hover:bg-[var(--ember)]/90 ember-glow">
            <Wand2 className="h-4 w-4 mr-2" /> Apply changes
          </Button>
        </div>
      </div>
    </div>
  );
}

function Control({ label, value, suffix, children }: { label: string; value: string; suffix: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-5">
      <div className="flex items-baseline justify-between">
        <p className="text-[10px] uppercase tracking-widest text-muted-foreground">{label}</p>
        <p className="font-mono text-sm">{value}<span className="text-muted-foreground">{suffix}</span></p>
      </div>
      <div className="mt-6">{children}</div>
    </div>
  );
}
