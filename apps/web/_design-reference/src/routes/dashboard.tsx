import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Plus, TrendingUp, Sparkles, Clock } from "lucide-react";
import { AppShell } from "@/components/app-shell";
import { EpisodeCard } from "@/components/episode-card";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { NARRATOR_PERSONAS } from "@/lib/mock-data";

export const Route = createFileRoute("/dashboard")({
  head: () => ({
    meta: [
      { title: "Dashboard · Daastaan AI" },
      { name: "description", content: "Continue processing, browse recent episodes and start a new story." },
      { property: "og:title", content: "Daastaan Dashboard" },
      { property: "og:description", content: "Your story studio at a glance." },
    ],
  }),
  component: Dashboard,
});

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-2xl border border-border bg-[var(--ink-2)]/60 p-5">
      <p className="text-[10px] uppercase tracking-widest text-muted-foreground">{label}</p>
      <p className="mt-2 font-display text-3xl">{value}</p>
      {hint && <p className="mt-1 text-xs text-[var(--signal)]">{hint}</p>}
    </div>
  );
}

function Dashboard() {
  const { data: stories = [] } = useQuery({ queryKey: ["stories"], queryFn: api.listStories });
  const processing = stories.filter((s) => s.status === "processing");
  const recent = stories.filter((s) => s.status !== "processing");

  return (
    <AppShell
      subtitle="Studio"
      title="Good evening, storyteller."
      actions={
        <Button asChild className="bg-[var(--ember)] text-[var(--ink)] hover:bg-[var(--ember)]/90 ember-glow">
          <Link to="/upload"><Plus className="h-4 w-4 mr-1" /> New Story</Link>
        </Button>
      }
    >
      <div className="grid gap-4 md:grid-cols-4">
        <Stat label="Episodes" value={String(stories.length)} hint="+2 this week" />
        <Stat label="Hours Rendered" value="12.4h" hint="+1.2h" />
        <Stat label="Voices Cast" value="18" />
        <Stat label="Avg Render" value="3m 42s" hint="-14% vs last week" />
      </div>

      {processing.length > 0 && (
        <section className="mt-12">
          <div className="flex items-center gap-2 mb-5">
            <Clock className="h-4 w-4 text-[var(--ember)]" />
            <h2 className="font-display text-xl">Continue processing</h2>
          </div>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {processing.map((s) => <EpisodeCard key={s.id} story={s} />)}
          </div>
        </section>
      )}

      <section className="mt-14">
        <div className="flex items-center gap-2 mb-5">
          <TrendingUp className="h-4 w-4 text-[var(--signal)]" />
          <h2 className="font-display text-xl">Recent episodes</h2>
        </div>
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {recent.map((s) => <EpisodeCard key={s.id} story={s} />)}
        </div>
      </section>

      <section className="mt-14">
        <div className="flex items-center gap-2 mb-5">
          <Sparkles className="h-4 w-4 text-[var(--ember)]" />
          <h2 className="font-display text-xl">Try a persona</h2>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {NARRATOR_PERSONAS.slice(0, 3).map((p) => (
            <Link
              key={p.id}
              to="/upload"
              className="group rounded-2xl border border-border bg-[var(--ink-2)]/60 p-5 hover:border-[var(--ember)]/40 transition-colors"
            >
              <div className="flex items-center gap-3">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: p.color }} />
                <span className="font-display text-lg">{p.name}</span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">{p.description}</p>
              <p className="mt-4 text-xs text-[var(--ember)] group-hover:underline">Cast in a new story →</p>
            </Link>
          ))}
        </div>
      </section>
    </AppShell>
  );
}
