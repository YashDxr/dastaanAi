import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { EpisodeCard } from "@/components/episode-card";
import { Input } from "@/components/ui/input";
import { Search, Grid3x3, List } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/library")({
  head: () => ({
    meta: [
      { title: "Library · Daastaan AI" },
      { name: "description", content: "Search, sort and revisit every episode in your studio." },
      { property: "og:title", content: "Library · Daastaan" },
      { property: "og:description", content: "Every story you've told." },
    ],
  }),
  component: LibraryPage,
});

const FILTERS = ["All", "Ready", "Processing", "Drafts"] as const;

function LibraryPage() {
  const { data: stories = [] } = useQuery({ queryKey: ["stories"], queryFn: api.listStories });
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<typeof FILTERS[number]>("All");
  const [view, setView] = useState<"grid" | "list">("grid");

  const filtered = stories.filter((s) => {
    if (q && !s.title.toLowerCase().includes(q.toLowerCase())) return false;
    if (filter === "Ready" && s.status !== "ready") return false;
    if (filter === "Processing" && s.status !== "processing") return false;
    if (filter === "Drafts" && s.status !== "draft") return false;
    return true;
  });

  return (
    <AppShell subtitle="Library" title="Every story you've told">
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search stories, characters, personas…" className="pl-9 bg-[var(--ink-2)]/60 border-border" />
        </div>
        <div className="flex items-center gap-1 rounded-full border border-border p-1 bg-[var(--ink-2)]/60">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                "px-3 py-1.5 rounded-full text-xs transition-colors",
                filter === f ? "bg-[var(--ember)] text-[var(--ink)]" : "text-muted-foreground hover:text-parchment"
              )}
            >
              {f}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1 rounded-full border border-border p-1 bg-[var(--ink-2)]/60">
          <button onClick={() => setView("grid")} className={cn("p-1.5 rounded-full", view === "grid" && "bg-white/5")} aria-label="Grid view">
            <Grid3x3 className="h-4 w-4" />
          </button>
          <button onClick={() => setView("list")} className={cn("p-1.5 rounded-full", view === "list" && "bg-white/5")} aria-label="List view">
            <List className="h-4 w-4" />
          </button>
        </div>
      </div>

      {view === "grid" ? (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((s) => <EpisodeCard key={s.id} story={s} />)}
        </div>
      ) : (
        <div className="rounded-2xl border border-border overflow-hidden">
          {filtered.map((s, i) => (
            <a
              key={s.id}
              href={s.status === "processing" ? `/processing/${s.id}` : `/story/${s.id}/player`}
              className={cn("grid grid-cols-[120px_minmax(0,1fr)_120px_100px] items-center gap-4 px-4 py-3 hover:bg-white/[0.03]", i > 0 && "border-t border-border")}
            >
              <div className="h-14 rounded-md" style={{ background: s.cover }} />
              <div className="min-w-0">
                <p className="font-display text-base truncate">{s.title}</p>
                <p className="text-xs text-muted-foreground">{s.author} · {s.genre}</p>
              </div>
              <span className="text-xs text-muted-foreground">{Math.round(s.duration / 60)}m</span>
              <span className="text-[10px] uppercase tracking-widest text-muted-foreground">{s.status}</span>
            </a>
          ))}
        </div>
      )}

      {filtered.length === 0 && (
        <div className="text-center py-16 text-muted-foreground">No stories match. Try another filter.</div>
      )}
    </AppShell>
  );
}
