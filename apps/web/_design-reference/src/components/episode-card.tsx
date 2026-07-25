import { Link } from "@tanstack/react-router";
import { Play, Loader2, FileText } from "lucide-react";
import type { Story } from "@/lib/mock-data";
import { WaveformSpine } from "./waveform-spine";
import { cn } from "@/lib/utils";

const fmt = (s: number) => `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;

export function EpisodeCard({ story }: { story: Story }) {
  const to = story.status === "processing" ? "/processing/$id" : "/story/$id/player";
  const StatusIcon = story.status === "processing" ? Loader2 : story.status === "draft" ? FileText : Play;

  return (
    <Link
      to={to}
      params={{ id: story.id }}
      className="group relative flex flex-col overflow-hidden rounded-2xl border border-border bg-card transition-all hover:border-[var(--ember)]/40 hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ember)]/60"
    >
      <div className="relative aspect-[16/10] overflow-hidden" style={{ background: story.cover }}>
        <div className="absolute inset-0 bg-gradient-to-t from-[var(--ink)]/95 via-[var(--ink)]/30 to-transparent" />
        <div className="absolute top-3 left-3 flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-widest text-parchment/80 px-2 py-1 rounded-full bg-black/30 backdrop-blur">
            {story.genre}
          </span>
        </div>
        <div className="absolute top-3 right-3">
          <span
            className={cn(
              "text-[10px] uppercase tracking-widest px-2 py-1 rounded-full backdrop-blur",
              story.status === "ready" && "bg-[var(--signal)]/20 text-[var(--signal)]",
              story.status === "processing" && "bg-[var(--ember)]/20 text-[var(--ember)]",
              story.status === "draft" && "bg-white/10 text-parchment/80",
              story.status === "failed" && "bg-destructive/20 text-destructive"
            )}
          >
            {story.status}
          </span>
        </div>
        <div className="absolute bottom-4 left-4 right-4">
          <WaveformSpine bars={64} seed={parseInt(story.id.slice(-1), 36) + 3} height={32} color="rgba(243,234,216,0.9)" />
        </div>
        <div className="absolute right-4 bottom-4 grid h-11 w-11 place-items-center rounded-full bg-[var(--ember)] text-[var(--ink)] shadow-lg opacity-0 translate-y-2 group-hover:opacity-100 group-hover:translate-y-0 transition-all ember-glow">
          <StatusIcon className={cn("h-4 w-4", story.status === "processing" && "animate-spin")} />
        </div>
      </div>
      <div className="p-5">
        <h3 className="font-display text-lg leading-snug line-clamp-1">{story.title}</h3>
        <p className="text-xs text-muted-foreground mt-1">{story.author} · {fmt(story.duration)} · {story.updatedAt}</p>
        {story.status === "processing" && typeof story.processingProgress === "number" && (
          <div className="mt-3">
            <div className="h-1 rounded-full bg-white/5 overflow-hidden">
              <div className="h-full bg-[var(--ember)]" style={{ width: `${story.processingProgress}%` }} />
            </div>
            <p className="mt-1 text-[10px] uppercase tracking-widest text-muted-foreground">
              {story.processingProgress}% · voice generation
            </p>
          </div>
        )}
      </div>
    </Link>
  );
}
