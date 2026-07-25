import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/app-shell";
import { PipelineGraph } from "@/components/pipeline-graph";
import { PIPELINE_NODES, LIVE_LOG_LINES, type NodeStatus, getStory } from "@/lib/mock-data";
import { Button } from "@/components/ui/button";
import { Link } from "@tanstack/react-router";
import { WaveformSpine } from "@/components/waveform-spine";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/processing/$id")({
  head: () => ({
    meta: [
      { title: "Rendering episode · Daastaan AI" },
      { name: "description", content: "Live pipeline for story understanding, casting and voice rendering." },
      { property: "og:title", content: "Rendering · Daastaan" },
      { property: "og:description", content: "The director's room." },
    ],
  }),
  component: ProcessingPage,
});

function ProcessingPage() {
  const { id } = Route.useParams();
  const story = getStory(id);
  const navigate = useNavigate();
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const i = setInterval(() => setTick((t) => t + 1), 900);
    return () => clearInterval(i);
  }, []);

  const status = useMemo<Record<string, NodeStatus>>(() => {
    const done = Math.min(PIPELINE_NODES.length, Math.floor(tick / 2));
    const map: Record<string, NodeStatus> = {};
    PIPELINE_NODES.forEach((n, i) => {
      map[n.id] = i < done ? "completed" : i === done ? "running" : "pending";
    });
    return map;
  }, [tick]);

  const doneCount = Object.values(status).filter((s) => s === "completed").length;
  const pct = Math.round((doneCount / PIPELINE_NODES.length) * 100);
  const eta = Math.max(0, Math.round((PIPELINE_NODES.length - doneCount) * 8));

  useEffect(() => {
    if (doneCount === PIPELINE_NODES.length) {
      const t = setTimeout(() => navigate({ to: "/story/$id/player", params: { id: story.id } }), 1400);
      return () => clearTimeout(t);
    }
  }, [doneCount, navigate, story.id]);

  const logs = LIVE_LOG_LINES.slice(0, Math.min(LIVE_LOG_LINES.length, doneCount + 1));

  return (
    <AppShell
      subtitle="Rendering"
      title={story.title}
      actions={
        <Button asChild variant="outline" className="border-parchment/20 bg-transparent text-parchment hover:bg-white/5">
          <Link to="/story/$id/player" params={{ id: story.id }}>Skip to preview</Link>
        </Button>
      }
    >
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-6">
          <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-4">
              <div>
                <p className="text-[10px] uppercase tracking-widest text-muted-foreground">Overall progress</p>
                <p className="font-display text-4xl mt-1">{pct}%</p>
              </div>
              <p className="text-xs text-muted-foreground">
                ETA <span className="font-mono text-parchment">{eta}s</span>
              </p>
            </div>
            <div className="mt-4">
              <WaveformSpine bars={140} seed={5} active progress={pct / 100} height={40} />
            </div>
          </div>

          <PipelineGraph status={status} />
        </div>

        <aside className="space-y-6">
          <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
            <p className="text-[10px] uppercase tracking-widest text-muted-foreground">Characters detected</p>
            <div className="mt-4 flex flex-col gap-3">
              {story.characters.slice(0, Math.max(1, doneCount)).map((c) => (
                <div key={c.id} className="flex items-center gap-3">
                  <span className="h-8 w-8 rounded-full grid place-items-center font-display text-sm" style={{ background: `${c.color}25`, color: c.color }}>
                    {c.name[0]}
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm truncate">{c.name}</p>
                    <p className="text-[11px] text-muted-foreground truncate">{c.role} · {c.lines} lines</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-0 overflow-hidden">
            <p className="text-[10px] uppercase tracking-widest text-muted-foreground px-5 pt-5">Live logs</p>
            <div className="mt-3 max-h-80 overflow-auto px-5 pb-5 space-y-2 font-mono text-[11px]">
              {logs.map((l, i) => (
                <div key={i} className="flex gap-2">
                  <span className={cn(
                    "shrink-0 px-1.5 rounded",
                    l.level === "warn" ? "bg-[var(--ember)]/20 text-[var(--ember)]" : "bg-white/5 text-muted-foreground"
                  )}>
                    {l.level}
                  </span>
                  <span className="text-muted-foreground shrink-0">{l.node}</span>
                  <span className="text-parchment/90">{l.msg}</span>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
