import { useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  RiAppsFill,
  RiCodeSSlashLine,
  RiDashboardFill,
  RiFileList3Fill,
  RiMoneyDollarCircleFill,
  RiNotification2Fill,
  RiPlugLine,
  RiServerFill,
  RiSettings3Fill,
  RiUser3Fill,
} from 'react-icons/ri'

const WORKSPACE_NAV_ITEMS = [
  {
    id: 'apps',
    label: 'Apps',
    path: '/apps',
    icon: RiAppsFill,
    exact: true,
  },
  {
    id: 'usage',
    label: 'Usage',
    path: '/usage',
    icon: RiServerFill,
    exact: true,
  },
  {
    id: 'operations',
    label: 'Operations',
    path: '/operations',
    icon: RiFileList3Fill,
    exact: true,
  },
  {
    id: 'billing',
    label: 'Billing & Hosting',
    path: '/billing',
    icon: RiMoneyDollarCircleFill,
    exact: true,
  },
  {
    id: 'settings',
    label: 'Workspace Settings',
    path: '/settings',
    icon: RiSettings3Fill,
    exact: true,
  },
]

const APP_NAV_ITEMS = [
  {
    id: 'overview',
    label: 'Overview',
    suffix: '/overview',
    icon: RiDashboardFill,
    exact: true,
  },
  {
    id: 'build',
    label: 'Build',
    suffix: '/build',
    icon: RiCodeSSlashLine,
    exact: true,
  },
  {
    id: 'deploy',
    label: 'Deploy',
    suffix: '/deploy',
    icon: RiServerFill,
    exact: true,
  },
  {
    id: 'users',
    label: 'Users',
    suffix: '/users',
    icon: RiUser3Fill,
    exact: true,
  },
  {
    id: 'integrations',
    label: 'Integrations',
    suffix: '/integrations',
    icon: RiPlugLine,
    exact: true,
  },
  {
    id: 'usage',
    label: 'Usage',
    suffix: '/usage',
    icon: RiServerFill,
    exact: true,
  },
  {
    id: 'operations',
    label: 'Operations',
    suffix: '/operations',
    icon: RiFileList3Fill,
    exact: true,
  },
  {
    id: 'settings',
    label: 'Settings',
    suffix: '/settings',
    icon: RiSettings3Fill,
    exact: true,
  },
  {
    id: 'admin',
    label: 'Admin',
    suffix: '/admin',
    icon: RiNotification2Fill,
    exact: true,
  },
]

function resolveAppId(pathname) {
  const match = /^\/apps\/([^/]+)/.exec(pathname)
  return match?.[1] ? decodeURIComponent(match[1]) : null
}

function buildAppPath(appId, suffix) {
  return `/apps/${encodeURIComponent(appId)}${suffix}`
}

function buildNavGroups(_adminSections = null, appId = null) {
  const groups = [
    {
      label: 'Console',
      items: WORKSPACE_NAV_ITEMS,
    },
  ]

  if (appId) {
    groups.push({
      label: 'App Console',
      items: APP_NAV_ITEMS.map((item) => ({
        ...item,
        path: buildAppPath(appId, item.suffix),
      })),
    })
  }

  return groups
}


function MenuGlyph() {
  return (
    <span className="flex h-5 w-5 flex-col justify-center gap-1" aria-hidden="true">
      <span className="h-0.5 w-5 rounded-full bg-current" />
      <span className="h-0.5 w-5 rounded-full bg-current" />
      <span className="h-0.5 w-5 rounded-full bg-current" />
    </span>
  )
}


function CloseGlyph() {
  return (
    <span className="relative block h-5 w-5" aria-hidden="true">
      <span className="absolute left-0 top-2 h-0.5 w-5 rotate-45 rounded-full bg-current" />
      <span className="absolute left-0 top-2 h-0.5 w-5 -rotate-45 rounded-full bg-current" />
    </span>
  )
}


function getUserLabel(user) {
  return user?.name || user?.email || user?.id || 'Admin'
}


function itemHref(item) {
  return item.path
}


function isItemActive(item, location) {
  if (item.exact) {
    return location.pathname === item.path
  }
  return location.pathname === item.path || location.pathname.startsWith(`${item.path}/`)
}


function AdminSidebar({ adminSections = null, onNavigate = null }) {
  const location = useLocation()
  const appId = resolveAppId(location.pathname)
  const navGroups = useMemo(() => buildNavGroups(adminSections, appId), [adminSections, appId])

  return (
    <aside className="rounded-lg border border-border bg-card p-3 shadow-sm">
      <div className="mb-5 flex items-center gap-3 px-2 pt-1">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-primary/30 bg-primary/10 text-sm font-bold text-primary">
          M
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">Mozaiks Console</div>
          <div className="truncate text-xs text-muted-foreground">Workspace console</div>
        </div>
      </div>

      <nav aria-label="Admin workspace" className="space-y-5">
        {navGroups.map((group) => (
          <div key={group.label}>
            <div className="mb-2 px-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              {group.label}
            </div>
            <div className="space-y-1">
              {group.items.map((item) => {
                const Icon = item.icon
                const active = isItemActive(item, location)
                return (
                  <Link
                    key={item.id}
                    to={itemHref(item)}
                    aria-current={active ? 'page' : undefined}
                    onClick={onNavigate || undefined}
                    className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition ${
                      active
                        ? 'border-primary/40 bg-primary/15 text-primary'
                        : 'border-transparent text-muted-foreground hover:border-border hover:bg-muted hover:text-foreground'
                    }`}
                  >
                    <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
                    <span className="min-w-0 truncate text-sm font-semibold text-current">{item.label}</span>
                  </Link>
                )
              })}
            </div>
          </div>
        ))}
      </nav>
    </aside>
  )
}


function AdminTopbar({ onOpenMenu }) {
  return (
    <header className="lg:hidden">
      <div className="flex items-center gap-3 px-1">
        <button
          type="button"
          onClick={onOpenMenu}
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-background text-foreground transition hover:bg-muted"
          aria-label="Open admin navigation"
        >
          <MenuGlyph />
        </button>
      </div>
    </header>
  )
}


export function AdminWorkspaceLayout({ children, adminSections = null }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="min-h-full flex-1 bg-background">
      <div className="mx-auto flex w-full max-w-[92rem] gap-6 px-4 py-6 md:px-6 lg:px-8">
        <div className="hidden w-72 shrink-0 lg:block">
          <div className="sticky top-24">
            <AdminSidebar adminSections={adminSections} />
          </div>
        </div>

        {mobileOpen ? (
          <div className="fixed inset-0 z-[70] lg:hidden">
            <button
              type="button"
              className="absolute inset-0 bg-black/50"
              aria-label="Close admin navigation"
              onClick={() => setMobileOpen(false)}
            />
            <div className="absolute bottom-4 left-4 top-24 w-[min(21rem,calc(100vw-2rem))] overflow-y-auto rounded-3xl border border-border bg-card/95 p-3 shadow-2xl backdrop-blur-md">
              <div className="mb-3 flex justify-end">
                <button
                  type="button"
                  onClick={() => setMobileOpen(false)}
                  className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-card text-foreground transition hover:bg-muted"
                  aria-label="Close admin navigation"
                >
                  <CloseGlyph />
                </button>
              </div>
              <AdminSidebar adminSections={adminSections} onNavigate={() => setMobileOpen(false)} />
            </div>
          </div>
        ) : null}

        <div className="min-w-0 flex-1 space-y-5">
          <AdminTopbar onOpenMenu={() => setMobileOpen(true)} />
          {children}
        </div>
      </div>
    </div>
  )
}


export default AdminWorkspaceLayout
