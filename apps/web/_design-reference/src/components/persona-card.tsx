import { motion } from "framer-motion";
import { Play, Pause } from "lucide-react";
import { useState } from "react";
import type { NarratorPersona } from "@/lib/mock-data";
import { WaveformSpine } from "./waveform-spine";
import { cn } from "@/lib/utils";

interface Props {
  persona: NarratorPersona;
  selected?: boolean;
  onSelect?: () => void;
  compact?: boolean;
}

export function PersonaCard({ persona, selected, onSelect, compact }: Props) {
  const [playing, setPlaying] = useState(false);
  return (
    <motion.button
      layout
      onClick={onSelect}
      className={cn(
        "text-left rounded-2xl border p-5 bg-[var(--ink-2)]/70 backdrop-blur relative overflow-hidden transition-all",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ember)]/60",
        selected ? "border-[var(--ember)]/70 ember-glow" : "border-border hover:border-parchment/25"
      )}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -top-16 -right-16 h-40 w-40 rounded-full opacity-30 blur-3xl"
        style={{ background: persona.color }}
      />
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-muted-foreground">Narrator</p>
          <h3 className="font-display text-xl mt-1">{persona.name}</h3>
          <p className="text-xs text-muted-foreground mt-1">{persona.vibe}</p>
        </div>
        <span
          className="grid h-9 w-9 place-items-center rounded-full text-[var(--ink)]"
          style={{ background: persona.color }}
          onClick={(e) => {
            e.stopPropagation();
            setPlaying((p) => !p);
          }}
          role="button"
          aria-label={playing ? "Pause preview" : "Play preview"}
        >
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4 translate-x-[1px]" />}
        </span>
      </div>

      {!compact && <p className="mt-4 text-sm text-parchment/85 leading-relaxed">"{persona.sample}"</p>}

      <div className="mt-4">
        <WaveformSpine bars={compact ? 40 : 64} seed={persona.id.length * 3 + 5} active={playing} height={compact ? 24 : 36} color={persona.color} />
      </div>
    </motion.button>
  );
}
