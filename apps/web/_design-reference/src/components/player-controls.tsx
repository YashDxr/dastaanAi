import { Play, Pause, SkipBack, SkipForward, Bookmark, Share2, Download } from "lucide-react";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { useEffect, useState } from "react";
import type { Story } from "@/lib/mock-data";
import { WaveformSpine } from "./waveform-spine";

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export function PlayerControls({ story }: { story: Story }) {
  const [playing, setPlaying] = useState(true);
  const [t, setT] = useState(story.duration * 0.28);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setT((v) => (v + 1) % story.duration), 1000);
    return () => clearInterval(id);
  }, [playing, story.duration]);

  return (
    <div className="rounded-2xl border border-border bg-[var(--ink-2)]/80 backdrop-blur p-6">
      <WaveformSpine
        bars={128}
        seed={story.id.length * 7}
        active={playing}
        progress={t / story.duration}
        height={72}
        onSeek={(p) => setT(p * story.duration)}
      />
      <div className="mt-3 flex items-center justify-between text-xs font-mono text-muted-foreground">
        <span>{fmt(t)}</span>
        <span>{fmt(story.duration)}</span>
      </div>

      <div className="mt-5 flex items-center justify-between gap-4">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" aria-label="Bookmark">
            <Bookmark className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon" aria-label="Share">
            <Share2 className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="icon" aria-label="Download">
            <Download className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" aria-label="Skip back" onClick={() => setT((v) => Math.max(0, v - 15))}>
            <SkipBack className="h-5 w-5" />
          </Button>
          <button
            onClick={() => setPlaying((p) => !p)}
            aria-label={playing ? "Pause" : "Play"}
            className="grid h-14 w-14 place-items-center rounded-full bg-[var(--ember)] text-[var(--ink)] ember-glow hover:scale-105 transition-transform"
          >
            {playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5 translate-x-[1px]" />}
          </button>
          <Button variant="ghost" size="icon" aria-label="Skip forward" onClick={() => setT((v) => Math.min(story.duration, v + 15))}>
            <SkipForward className="h-5 w-5" />
          </Button>
        </div>

        <div className="hidden md:flex items-center gap-3 min-w-[180px]">
          <span className="text-[10px] uppercase tracking-widest text-muted-foreground">Music</span>
          <Slider defaultValue={[35]} max={100} step={1} className="w-24" />
        </div>
      </div>
    </div>
  );
}
