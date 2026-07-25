import type { ReactNode } from 'react'
import { NavigationRail } from './navigation-rail'
export function AppShell({ children, title, subtitle, actions, path, go }: { children: ReactNode; title?: string; subtitle?: string; actions?: ReactNode; path: string; go: (to: string) => void }) { return <div className="app-shell"><NavigationRail path={path} go={go} /><div className="app-content">{(title || actions) && <header className="app-header"><div>{subtitle && <p className="eyebrow">{subtitle}</p>}{title && <h1>{title}</h1>}</div>{actions}</header>}<main>{children}</main></div></div> }
