import { createFileRoute, Outlet, Link, useRouterState } from "@tanstack/react-router";
import { AppShell } from "@/components/app-shell";
import { getStory } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/story/$id")({
  component: StoryLayout,
});

const TABS = [
  { to: "/story/$id/player" as const, label: "Player" },
  { to: "/story/$id/understanding" as const, label: "Understanding" },
  { to: "/story/$id/personas" as const, label: "Personas" },
  { to: "/story/$id/voices" as const, label: "Voices" },
];

function StoryLayout() {
  const { id } = Route.useParams();
  const story = getStory(id);
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <AppShell
      subtitle={`${story.author} · ${story.genre}`}
      title={story.title}
      actions={
        <div className="hidden md:flex items-center gap-1 rounded-full border border-border p-1 bg-[var(--ink-2)]/60">
          {TABS.map((t) => {
            const active = pathname.endsWith(t.to.split("/").pop()!);
            return (
              <Link
                key={t.to}
                to={t.to}
                params={{ id }}
                className={cn(
                  "px-4 py-1.5 rounded-full text-xs transition-colors",
                  active ? "bg-[var(--ember)] text-[var(--ink)]" : "text-muted-foreground hover:text-parchment"
                )}
              >
                {t.label}
              </Link>
            );
          })}
        </div>
      }
    >
      <div className="md:hidden mb-6 flex flex-wrap gap-2">
        {TABS.map((t) => {
          const active = pathname.endsWith(t.to.split("/").pop()!);
          return (
            <Link
              key={t.to}
              to={t.to}
              params={{ id }}
              className={cn(
                "px-3 py-1.5 rounded-full text-xs border",
                active ? "bg-[var(--ember)] text-[var(--ink)] border-[var(--ember)]" : "border-border text-muted-foreground"
              )}
            >
              {t.label}
            </Link>
          );
        })}
      </div>
      <Outlet />
    </AppShell>
  );
}

