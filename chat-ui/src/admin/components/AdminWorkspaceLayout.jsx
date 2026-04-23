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
  RiRobot2Line,
  RiServerFill,
  RiSettings3Fill,
  RiUser3Fill,
} from 'react-icons/ri'

import { useChatUI } from '../../context/ChatUIContext'


const NAV_GROUPS = [
  {
    label: 'Administration',
    items: [
      {
        id: 'overview',
        label: 'Overview',
        path: '/admin',
        icon: RiDashboardFill,
        exact: true,
      },
      {
        id: 'users',
        label: 'Users',
        path: '/admin/users',
        icon: RiUser3Fill,
      },
      {
        id: 'billing',
        label: 'Billing',
        path: '/admin/billing',
        icon: RiMoneyDollarCircleFill,
      },
      {
        id: 'usage',
        label: 'Usage',
        path: '/admin/usage',
        icon: RiServerFill,
      },
      {
        id: 'activity',
        label: 'Activity',
        path: '/admin/activity',
        icon: RiFileList3Fill,
      },
      {
        id: 'settings',
        label: 'Settings',
        path: '/admin/settings',
        icon: RiSettings3Fill,
      },
      {
        id: 'integrations',
        label: 'Integrations',
        path: '/admin/integrations',
        icon: RiAppsFill,
      },
      {
        id: 'support',
        label: 'Support',
        path: '/admin/support',
        icon: RiNotification2Fill,
      },
    ],
  },
  {
    label: 'Builder',
    items: [
      {
        id: 'studio',
        label: 'Studio',
        path: '/studio',
        icon: RiRobot2Line,
        exact: true,
      },
      {
        id: 'build',
        label: 'Build',
        path: '/studio/build',
        icon: RiCodeSSlashLine,
        exact: true,
      },
      {
        id: 'adapters',
        label: 'Adapters',
        path: '/studio/adapters',
        icon: RiPlugLine,
        exact: true,
      },
    ],
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


function findActiveItem(location) {
  return NAV_GROUPS.flatMap((group) => group.items).find((item) => isItemActive(item, location))
}


function AdminSidebar({ onNavigate = null }) {
  const location = useLocation()

  return (
    <aside className="rounded-lg border border-border bg-card p-3 shadow-sm">
      <div className="mb-5 flex items-center gap-3 px-2 pt-1">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-primary/30 bg-primary/10 text-sm font-bold text-primary">
          M
        </div>
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">Mozaiks Admin</div>
          <div className="truncate text-xs text-muted-foreground">Control workspace</div>
        </div>
      </div>

      <nav aria-label="Admin workspace" className="space-y-5">
        {NAV_GROUPS.map((group) => (
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
  const location = useLocation()
  const { user } = useChatUI()
  const activeItem = useMemo(() => findActiveItem(location), [location])
  const userLabel = getUserLabel(user)

  return (
    <header className="flex items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3 shadow-sm">
      <div className="flex min-w-0 items-center gap-3">
        <button
          type="button"
          onClick={onOpenMenu}
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-background text-foreground transition hover:bg-muted lg:hidden"
          aria-label="Open admin navigation"
        >
          <MenuGlyph />
        </button>
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            Admin Dashboard
          </div>
          <h1 className="truncate text-lg font-semibold text-foreground">
            {activeItem?.label || 'Overview'}
          </h1>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <div className="hidden text-right sm:block">
          <div className="max-w-40 truncate text-sm font-semibold text-foreground">{userLabel}</div>
          <div className="text-xs text-muted-foreground">admin</div>
        </div>
        <div className="h-10 w-10 rounded-lg border border-primary/30 bg-primary/15" aria-hidden="true" />
      </div>
    </header>
  )
}


export function AdminWorkspaceLayout({ children }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="min-h-full flex-1 bg-background">
      <div className="mx-auto flex w-full max-w-[92rem] gap-6 px-4 py-6 md:px-6 lg:px-8">
        <div className="hidden w-72 shrink-0 lg:block">
          <div className="sticky top-24">
            <AdminSidebar />
          </div>
        </div>

        {mobileOpen ? (
          <div className="fixed inset-0 z-[60] lg:hidden">
            <button
              type="button"
              className="absolute inset-0 bg-black/50"
              aria-label="Close admin navigation"
              onClick={() => setMobileOpen(false)}
            />
            <div className="absolute bottom-4 left-4 top-4 w-[min(20rem,calc(100vw-2rem))] overflow-y-auto rounded-lg bg-background p-3 shadow-xl">
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
              <AdminSidebar onNavigate={() => setMobileOpen(false)} />
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
