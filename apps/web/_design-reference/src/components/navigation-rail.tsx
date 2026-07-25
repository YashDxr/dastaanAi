import { Link, useRouterState } from "@tanstack/react-router";
import {
  Home,
  Upload,
  Library,
  Activity,
  Settings,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { WaveformSpine } from "./waveform-spine";

const items = [
  { to: "/dashboard", icon: Home, label: "Dashboard" },
  { to: "/upload", icon: Upload, label: "New Story" },
  { to: "/library", icon: Library, label: "Library" },
  { to: "/observability", icon: Activity, label: "Observability" },
  { to: "/settings", icon: Settings, label: "Settings" },
] as const;

export function NavigationRail() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <aside className="sticky top-0 hidden md:flex h-dvh w-[240px] shrink-0 flex-col border-r border-border bg-[var(--ink-2)]/70 backdrop-blur-xl">
      <Link to="/" className="flex items-center gap-3 px-6 pt-7 pb-8 group">
        <div className="grid h-10 w-10 place-items-center rounded-lg bg-[var(--ember)]/15 ring-1 ring-[var(--ember)]/30">
          <Sparkles className="h-5 w-5 text-[var(--ember)]" />
        </div>
        <div className="flex flex-col leading-tight">
          <span className="font-display text-lg text-parchment">Daastaan</span>
          <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Story Studio</span>
        </div>
      </Link>

      <nav className="flex-1 px-3 flex flex-col gap-1" aria-label="Primary">
        {items.map(({ to, icon: Icon, label }) => {
          const active = pathname === to || (to !== "/dashboard" && pathname.startsWith(to));
          return (
            <Link
              key={to}
              to={to}
              className={cn(
                "group flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ember)]/60",
                active
                  ? "bg-[var(--ember)]/12 text-parchment"
                  : "text-muted-foreground hover:text-parchment hover:bg-white/[0.03]"
              )}
            >
              <Icon className={cn("h-4 w-4 shrink-0", active && "text-[var(--ember)]")} />
              <span>{label}</span>
              {active && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-[var(--ember)]" />}
            </Link>
          );
        })}
      </nav>

      <div className="mx-3 mb-4 rounded-xl border border-border/70 bg-[var(--ink-3)]/60 p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] uppercase tracking-widest text-muted-foreground">Now Rendering</span>
          <span className="h-2 w-2 rounded-full bg-[var(--signal)] animate-pulse" />
        </div>
        <WaveformSpine bars={36} seed={41} active height={28} color="var(--signal)" />
        <p className="mt-2 text-xs text-parchment/80 truncate">Nights on Kolachi Street</p>
        <p className="text-[10px] text-muted-foreground">Voice · scene 3 of 5</p>
      </div>
    </aside>
  );
}
