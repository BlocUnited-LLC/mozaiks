/**
 * AdminWorkspaceLayout — sidebar shell for the admin portal.
 *
 * Nav is fully API-driven: pages come from GET /api/admin/config `pages` array,
 * which is loaded from app/admin/admin_registry.yaml at runtime.
 *
 * Workspace-scope pages (scope: workspace) build the top-level Studio nav.
 * App-scope pages (scope: app) build the per-app nav when appId is present.
 *
 * No hardcoded nav items or section taxonomies.
 */
import { useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useNavigation } from '../../providers/NavigationProvider.jsx'
import {
  RiAppsFill,
  RiCustomerServiceFill,
  RiDashboardFill,
  RiFileList3Fill,
  RiHistoryFill,
  RiMoneyDollarCircleFill,
  RiPlugLine,
  RiPulseLine,
  RiServerFill,
  RiSettings3Fill,
  RiUser3Fill,
} from 'react-icons/ri'

// Icon map — matches icon hint strings from admin_registry.yaml
const ICON_MAP = {
  apps:       RiAppsFill,
  chart:      RiFileList3Fill,
  pulse:      RiPulseLine,
  billing:    RiMoneyDollarCircleFill,
  server:     RiServerFill,
  dashboard:  RiDashboardFill,
  access:     RiUser3Fill,
  operations: RiServerFill,
  settings:   RiSettings3Fill,
  plug:       RiPlugLine,
  history:    RiHistoryFill,
  support:    RiCustomerServiceFill,
}

function resolveIcon(iconHint) {
  return ICON_MAP[iconHint] || RiDashboardFill
}

function resolveShellLabel(appName, appId) {
  const trimmed = typeof appName === 'string' ? appName.trim() : ''
  if (trimmed) return trimmed
  return appId ? 'App Admin' : 'Studio'
}

function resolveShellSubtext(appId) {
  return appId ? 'App admin' : 'Manage all apps'
}

function resolveShellMonogram(label) {
  const first = String(label || '').trim().charAt(0).toUpperCase()
  return first || 'S'
}

function resolveAppId(pathname) {
  const match = /^\/apps\/([^/]+)/.exec(pathname)
  const appId = match?.[1] ? decodeURIComponent(match[1]) : null
  return appId && appId !== 'new' ? appId : null
}

function buildAppPath(appId, templatePath) {
  return templatePath.replace(':appId', encodeURIComponent(appId))
}

/**
 * Build nav groups from the registry pages array.
 * When appId is present, show app-scope pages with appId substituted into paths.
 * Otherwise, show workspace-scope pages as the top-level Studio nav.
 */
function buildNavGroups(adminPages, appId) {
  if (!adminPages?.length) {
    // Fallback before API responds — show minimal workspace nav
    return [
      {
        label: null,
        items: [
          { id: 'apps', label: 'Apps', path: '/apps', icon: RiAppsFill, exact: true },
        ],
      },
    ]
  }

  if (appId) {
    const appPages = adminPages
      .filter((p) => p.scope === 'app' && p.enabled !== false && p.show_in_navigation !== false)
      .sort((a, b) => a.order - b.order)
      .map((p) => ({
        id: p.id,
        label: p.label,
        path: buildAppPath(appId, p.path),
        icon: resolveIcon(p.icon),
        exact: true,
      }))
    return [{ label: null, items: appPages }]
  }

  const workspacePages = adminPages
    .filter((p) => p.scope === 'workspace' && p.enabled !== false && p.show_in_navigation !== false)
    .sort((a, b) => a.order - b.order)
    .map((p) => ({
      id: p.id,
      label: p.label,
      path: p.path,
      icon: resolveIcon(p.icon),
      exact: true,
    }))
  return [{ label: null, items: workspacePages }]
}

function isItemActive(item, location) {
  if (item.exact) return location.pathname === item.path
  return location.pathname === item.path || location.pathname.startsWith(`${item.path}/`)
}

function getActiveNavItem(navGroups, location) {
  for (const group of navGroups) {
    const item = group.items.find((c) => isItemActive(c, location))
    if (item) return { group, item }
  }
  return { group: navGroups[0] || null, item: navGroups[0]?.items?.[0] || null }
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

function AdminSidebar({
  adminPages = null,
  onNavigate = null,
  navGroups: providedNavGroups = null,
  surface = 'sidebar',
  shellLabel = 'Studio',
  shellSubtext = 'Manage all apps',
}) {
  const location = useLocation()
  const appId = resolveAppId(location.pathname)
  const derivedNavGroups = useMemo(() => buildNavGroups(adminPages, appId), [adminPages, appId])
  const navGroups = providedNavGroups || derivedNavGroups
  const navigationLabel = `${shellLabel} navigation`
  const surfaceClass =
    surface === 'sheet'
      ? 'rounded-2xl border border-border/45 bg-background/76 p-3'
      : 'rounded-[1.35rem] border border-border/42 bg-card/22 p-3 shadow-sm shadow-black/5'

  return (
    <aside className={surfaceClass}>
      {appId ? (
        <div className="mb-4 px-2 pt-1">
          <Link
            to="/apps"
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground/80 transition hover:text-foreground"
          >
            <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 12H5M12 5l-7 7 7 7" />
            </svg>
            All Apps
          </Link>
        </div>
      ) : (
        <div className="mb-5 flex items-center gap-3 px-2 pt-1">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-primary/26 bg-primary/8 text-sm font-bold text-primary">
            {resolveShellMonogram(shellLabel)}
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-foreground">{shellLabel}</div>
            <div className="truncate text-xs text-muted-foreground/84">{shellSubtext}</div>
          </div>
        </div>
      )}

      <nav aria-label={navigationLabel} className="space-y-5">
        {navGroups.map((group) => (
          <div key={group.label || 'workspace'}>
            {group.label ? (
              <div className="mb-2 px-2 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground/72">
                {group.label}
              </div>
            ) : null}
            <div className="space-y-1">
              {group.items.map((item) => {
                const Icon = item.icon
                const active = isItemActive(item, location)
                return (
                  <Link
                    key={item.id}
                    to={item.path}
                    aria-current={active ? 'page' : undefined}
                    onClick={onNavigate || undefined}
                    className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
                      active
                        ? 'border-primary/28 bg-primary/10 text-primary'
                        : 'border-transparent text-muted-foreground/88 hover:border-border/45 hover:bg-muted/28 hover:text-foreground'
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

function AdminMobileNavTrigger({ onOpenMenu, activeLabel = 'Studio', shellLabel = 'Studio' }) {
  return (
    <div className="sticky top-[calc(env(safe-area-inset-top,0px)+4.5rem)] z-30 mb-4 lg:hidden">
      <button
        type="button"
        onClick={onOpenMenu}
        className="flex w-full items-center justify-between gap-3 rounded-2xl border border-border/45 bg-background/92 px-4 py-3 text-left text-foreground shadow-lg shadow-black/15 backdrop-blur-md transition hover:bg-muted/35"
        aria-label={`Open ${shellLabel} navigation`}
      >
        <span className="flex min-w-0 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-primary/24 bg-primary/10 text-primary shadow-sm shadow-primary/10">
            <MenuGlyph />
          </span>
          <span className="min-w-0">
            <span className="block text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/84">
              {shellLabel} navigation
            </span>
            <span className="block truncate text-sm font-semibold text-foreground">
              {activeLabel}
            </span>
          </span>
        </span>
        <svg className="h-4 w-4 shrink-0 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m9 6 6 6-6 6" />
        </svg>
      </button>
    </div>
  )
}

export function AdminWorkspaceLayout({ children, adminPages = null }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const { appName } = useNavigation()
  const appId = resolveAppId(location.pathname)
  const shellLabel = resolveShellLabel(appName, appId)
  const shellSubtext = resolveShellSubtext(appId)
  const navGroups = useMemo(() => buildNavGroups(adminPages, appId), [adminPages, appId])
  const activeNav = useMemo(() => getActiveNavItem(navGroups, location), [navGroups, location])
  const activeLabel = activeNav.item?.label || activeNav.group?.label || 'Studio'

  return (
    <div className="min-h-full flex-1 bg-background">
      <div className="mx-auto flex w-full max-w-[96rem] gap-6 px-4 py-7 md:px-6 lg:px-8">
        <div className="hidden w-72 shrink-0 lg:block">
          <div className="sticky top-24">
            <AdminSidebar
              adminPages={adminPages}
              navGroups={navGroups}
              shellLabel={shellLabel}
              shellSubtext={shellSubtext}
            />
          </div>
        </div>

        {mobileOpen ? (
          <div className="fixed inset-0 z-[90] lg:hidden">
            <button
              type="button"
              className="absolute inset-0 bg-black/50"
              aria-label="Close Studio navigation"
              onClick={() => setMobileOpen(false)}
            />
            <div className="absolute inset-x-0 bottom-0 max-h-[82dvh] overflow-y-auto rounded-t-3xl border border-border bg-card/95 p-3 pb-[calc(env(safe-area-inset-bottom,0px)+6.25rem)] shadow-2xl backdrop-blur-md">
              <div className="mb-3 flex items-center justify-between px-2">
                <div className="min-w-0">
                  {appId ? (
                    <Link
                      to="/apps"
                      onClick={() => setMobileOpen(false)}
                      className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground/80 transition hover:text-foreground"
                    >
                      <svg className="h-3.5 w-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M19 12H5M12 5l-7 7 7 7" />
                      </svg>
                      All Apps
                    </Link>
                  ) : (
                    <div className="truncate text-sm font-semibold text-foreground">Studio navigation</div>
                  )}
                  <div className="truncate text-xs text-muted-foreground">{activeLabel}</div>
                </div>
                <button
                  type="button"
                  onClick={() => setMobileOpen(false)}
                  className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-card text-foreground transition hover:bg-muted"
                  aria-label="Close Studio navigation"
                >
                  <CloseGlyph />
                </button>
              </div>
              <AdminSidebar
                adminPages={adminPages}
                navGroups={navGroups}
                onNavigate={() => setMobileOpen(false)}
                surface="sheet"
                shellLabel={shellLabel}
                shellSubtext={shellSubtext}
              />
            </div>
          </div>
        ) : null}

        <div className="min-w-0 flex-1 space-y-5 pb-10 md:pb-10 lg:pb-0">
          <AdminMobileNavTrigger
            onOpenMenu={() => setMobileOpen(true)}
            activeLabel={activeLabel}
            shellLabel={shellLabel}
          />
          {children}
        </div>
      </div>
    </div>
  )
}

export default AdminWorkspaceLayout
