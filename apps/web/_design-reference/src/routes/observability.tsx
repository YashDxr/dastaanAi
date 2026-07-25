import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/app-shell";
import { PipelineGraph } from "@/components/pipeline-graph";
import { LIVE_LOG_LINES, PIPELINE_NODES, type NodeStatus } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/observability")({
  head: () => ({
    meta: [
      { title: "Observability · Daastaan AI" },
      { name: "description", content: "Traces, queue depth and pipeline health for every render." },
      { property: "og:title", content: "Observability · Daastaan" },
      { property: "og:description", content: "LangSmith meets Linear." },
    ],
  }),
  component: ObservabilityPage,
});

const status: Record<string, NodeStatus> = Object.fromEntries(
  PIPELINE_NODES.map((n, i) => [n.id, i < 6 ? "completed" : i === 6 ? "running" : "pending"])
);

const TRACES = [
  { node: "understand", label: "Story Understanding", ms: 812, retries: 0 },
  { node: "registry", label: "Character Registry", ms: 421, retries: 0 },
  { node: "dialogue", label: "Dialogue Split", ms: 638, retries: 1 },
  { node: "emotion", label: "Emotion Detection", ms: 1204, retries: 0 },
  { node: "persona", label: "Narrator Persona", ms: 302, retries: 0 },
  { node: "voice", label: "Voice Generation", ms: 4218, retries: 2 },
  { node: "music", label: "Music Generation", ms: 3801, retries: 0 },
  { node: "assembly", label: "Assembly", ms: 1188, retries: 0 },
];

function ObservabilityPage() {
  const total = TRACES.reduce((a, t) => a + t.ms, 0);

  return (
    <AppShell subtitle="Ops" title="Observability">
      <div className="grid gap-4 md:grid-cols-4 mb-8">
        <Metric label="Active jobs" value="3" hint="2 rendering" />
        <Metric label="Queue depth" value="12" hint="p95 4.2s wait" />
        <Metric label="P95 latency" value="4.2s" hint="voice.gen" />
        <Metric label="Error rate" value="0.4%" hint="last 24h" />
      </div>

      <PipelineGraph status={status} />

      <div className="grid gap-6 mt-8 lg:grid-cols-[minmax(0,1fr)_360px]">
        {/* Trace waterfall */}
        <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-6">
          <div className="flex items-center justify-between mb-5">
            <p className="text-[10px] uppercase tracking-widest text-muted-foreground">Trace · job_2f8a</p>
            <p className="text-xs font-mono text-muted-foreground">{total.toLocaleString()} ms</p>
          </div>
          <div className="space-y-2.5">
            {TRACES.map((t, i) => {
              const offset = TRACES.slice(0, i).reduce((a, x) => a + x.ms, 0);
              const left = (offset / total) * 100;
              const width = (t.ms / total) * 100;
              return (
                <div key={t.node} className="grid grid-cols-[160px_minmax(0,1fr)_80px] items-center gap-3 text-xs">
                  <span className="truncate text-muted-foreground">{t.label}</span>
                  <div className="relative h-5 rounded-md bg-white/[0.03]">
                    <div
                      className={cn(
                        "absolute h-full rounded-md",
                        t.retries > 0 ? "bg-[var(--ember)]/70" : "bg-[var(--signal)]/50"
                      )}
                      style={{ left: `${left}%`, width: `${width}%` }}
                    />
                  </div>
                  <span className="font-mono text-right text-muted-foreground">{t.ms}ms</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Logs */}
        <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-0 overflow-hidden">
          <p className="text-[10px] uppercase tracking-widest text-muted-foreground px-5 pt-5">Live logs</p>
          <div className="mt-3 max-h-[420px] overflow-auto px-5 pb-5 space-y-2 font-mono text-[11px]">
            {[...LIVE_LOG_LINES, ...LIVE_LOG_LINES].map((l, i) => (
              <div key={i} className="flex gap-2">
                <span className={cn(
                  "shrink-0 px-1.5 rounded",
                  l.level === "warn" ? "bg-[var(--ember)]/20 text-[var(--ember)]" : "bg-white/5 text-muted-foreground"
                )}>{l.level}</span>
                <span className="text-muted-foreground shrink-0">{l.node}</span>
                <span className="text-parchment/90">{l.msg}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </AppShell>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-5">
      <p className="text-[10px] uppercase tracking-widest text-muted-foreground">{label}</p>
      <p className="mt-2 font-display text-3xl">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
