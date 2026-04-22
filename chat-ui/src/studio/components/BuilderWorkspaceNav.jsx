import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'


const NAV_ITEMS = [
  {
    id: 'admin',
    label: 'Admin Portal',
    description: 'App, module, and runtime controls.',
    path: '/admin',
  },
  {
    id: 'studio',
    label: 'Studio',
    description: 'Workspace status and readiness.',
    path: '/studio',
  },
  {
    id: 'build',
    label: 'Build',
    description: 'Draft requests and launch workflows.',
    path: '/studio/build',
  },
]


function MenuGlyph() {
  return (
    <span className="flex h-5 w-5 flex-col justify-center gap-1" aria-hidden="true">
      <span className="h-0.5 w-5 rounded-full bg-current" />
      <span className="h-0.5 w-5 rounded-full bg-current" />
      <span className="h-0.5 w-5 rounded-full bg-current" />
    </span>
  )
}


export default function BuilderWorkspaceNav({ className = '', id = null, onNavigate = null }) {
  const location = useLocation()

  return (
    <nav
      aria-label="Builder workspace"
      id={id || undefined}
      className={`rounded-2xl border border-border bg-card p-3 shadow-sm ${className}`}
    >
      <div className="px-2 pb-3">
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          Workspace
        </div>
      </div>
      <div className="space-y-1">
        {NAV_ITEMS.map((item) => {
          const active = location.pathname === item.path
          return (
            <Link
              key={item.id}
              to={item.path}
              aria-current={active ? 'page' : undefined}
              onClick={onNavigate || undefined}
              className={`block rounded-xl border px-3 py-3 text-left transition ${
                active
                  ? 'border-primary/40 bg-primary/15 text-primary'
                  : 'border-transparent text-muted-foreground hover:border-border hover:bg-muted hover:text-foreground'
              }`}
            >
              <span className="block text-sm font-semibold text-current">{item.label}</span>
              <span className="mt-1 block text-xs leading-5 opacity-80">{item.description}</span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}


export function BuilderWorkspaceLayout({ children }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="min-h-full flex-1 bg-background">
      <div className="mx-auto flex w-full max-w-7xl flex-1 gap-6 px-4 py-6 md:px-6 lg:px-8">
        <aside className="hidden w-64 shrink-0 lg:block">
          <div className="sticky top-24">
            <BuilderWorkspaceNav />
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          <div className="mb-4 lg:hidden">
            <button
              type="button"
              aria-expanded={mobileOpen}
              aria-controls="builder-workspace-mobile-nav"
              onClick={() => setMobileOpen((open) => !open)}
              className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-sm font-semibold text-foreground shadow-sm transition hover:bg-muted"
            >
              <MenuGlyph />
              Workspace
            </button>
            {mobileOpen ? (
              <BuilderWorkspaceNav
                className="mt-3"
                id="builder-workspace-mobile-nav"
                onNavigate={() => setMobileOpen(false)}
              />
            ) : null}
          </div>

          {children}
        </div>
      </div>
    </div>
  )
}
