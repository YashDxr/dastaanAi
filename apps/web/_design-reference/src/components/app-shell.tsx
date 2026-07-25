import { type ReactNode } from "react";
import { NavigationRail } from "./navigation-rail";

interface AppShellProps {
  children: ReactNode;
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
}

export function AppShell({ children, title, subtitle, actions }: AppShellProps) {
  return (
    <div className="min-h-dvh flex bg-[var(--ink)] text-parchment grain">
      <NavigationRail />
      <div className="flex-1 min-w-0 flex flex-col">
        {(title || actions) && (
          <header className="sticky top-0 z-20 border-b border-border/70 bg-[var(--ink)]/80 backdrop-blur-xl">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 px-6 md:px-10 py-5">
              <div className="min-w-0">
                {subtitle && (
                  <p className="text-[10px] uppercase tracking-[0.24em] text-muted-foreground mb-1">
                    {subtitle}
                  </p>
                )}
                {title && (
                  <h1 className="font-display text-2xl md:text-3xl truncate">{title}</h1>
                )}
              </div>
              {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
            </div>
          </header>
        )}
        <main className="flex-1 px-6 md:px-10 py-8">{children}</main>
      </div>
    </div>
  );
}
