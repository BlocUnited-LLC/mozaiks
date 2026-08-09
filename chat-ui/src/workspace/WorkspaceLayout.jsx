import { useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  RiAppsFill,
  RiDashboardFill,
  RiFileList3Fill,
  RiMoneyDollarCircleFill,
  RiPlugLine,
  RiPulseLine,
  RiServerFill,
  RiUser3Fill,
} from 'react-icons/ri'
import { useNavigation } from '../providers/NavigationProvider.jsx'
import { useChatUI } from '../context/ChatUIContext.jsx'
import { getUserRoles, isShellItemVisible } from '../navigation/shellActions.js'

const ICON_MAP = {
  apps: RiAppsFill,
  dashboard: RiDashboardFill,
  chart: RiFileList3Fill,
  billing: RiMoneyDollarCircleFill,
  plug: RiPlugLine,
  pulse: RiPulseLine,
  server: RiServerFill,
  users: RiUser3Fill,
}

function resolveAppId(pathname) {
  const match = /^\/apps\/([^/]+)/.exec(pathname)
  const appId = match?.[1] ? decodeURIComponent(match[1]) : null
  return appId && appId !== 'new' ? appId : null
}

function resolvePageNavigation(page) {
  return page?.meta?.navigation && typeof page.meta.navigation === 'object'
    ? page.meta.navigation
    : {}
}

function buildPathForPage(path, appId) {
  if (typeof path !== 'string' || !path.startsWith('/')) return null
  if (!appId) return path
  return path.replace(':appId', encodeURIComponent(appId))
}

function routeIdFromPath(path) {
  return path.replace(/^\//, '').replace(/[:/]+/g, '-').replace(/-+/g, '-') || 'studio'
}

function resolveIcon(iconHint) {
  return ICON_MAP[iconHint] || RiDashboardFill
}

function resolveShellLabel(appName, appId) {
  const trimmed = typeof appName === 'string' ? appName.trim() : ''
  if (trimmed) return trimmed
  return appId ? 'App Workspace' : 'Studio'
}

function resolveShellSubtext(appId) {
  return appId ? 'App workspace' : 'Manage your apps'
}

function resolveShellMonogram(label) {
  const first = String(label || '').trim().charAt(0).toUpperCase()
  return first || 'S'
}

function buildNavGroupsFromPages(pages, appId = null, roles = []) {
  const group = appId ? 'app-studio' : 'workspace-studio'
  const items = (Array.isArray(pages) ? pages : [])
    .filter((page) => page && page.meta?.appShell)
    .filter((page) => isShellItemVisible(page, roles))
    .filter((page) => resolvePageNavigation(page).group === group)
    .filter((page) => resolvePageNavigation(page).include !== false)
    .map((page) => {
      const navigation = resolvePageNavigation(page)
      const path = buildPathForPage(page.path, appId)
      if (!path) return null
      return {
        id: navigation.id || page.id || page.component || routeIdFromPath(page.path),
        label: navigation.label || page.label || 'Studio',
        path,
        icon: resolveIcon(navigation.icon),
        exact: true,
        order: Number.isFinite(page.order) ? page.order : 500,
      }
    })
    .filter(Boolean)
    .sort((left, right) => (
      left.order - right.order || left.label.localeCompare(right.label)
    ))

  return [{ label: null, items }]
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

function itemHref(item) {
  return item.path
}

function isItemActive(item, location) {
  if (item.exact) {
    return location.pathname === item.path
  }
  return location.pathname === item.path || location.pathname.startsWith(`${item.path}/`)
}

function getActiveNavItem(navGroups, location) {
  for (const group of navGroups) {
    const item = group.items.find((candidate) => isItemActive(candidate, location))
    if (item) {
      return { group, item }
    }
  }

  return { group: navGroups[0] || null, item: navGroups[0]?.items?.[0] || null }
}

function WorkspaceSidebar({
  appId = null,
  navGroups,
  onNavigate = null,
  surface = 'sidebar',
  shellLabel = 'Studio',
  shellSubtext = 'Manage your apps',
}) {
  const location = useLocation()
  const navigationLabel = 'Workspace navigation'
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
                    to={itemHref(item)}
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

function WorkspaceMobileNavTrigger({ onOpenMenu, activeLabel = 'Studio', shellLabel = 'Studio' }) {
  const openMenuLabel = `Open ${shellLabel} navigation`

  return (
    <div className="fixed bottom-[calc(env(safe-area-inset-bottom,0px)+5.5rem)] left-4 z-[56] lg:hidden">
      <button
        type="button"
        onClick={onOpenMenu}
        className="inline-flex h-11 max-w-[calc(100vw-2rem)] items-center gap-2 rounded-full border border-border/45 bg-background/90 px-4 text-foreground shadow-lg shadow-black/15 backdrop-blur-md transition hover:bg-muted/35"
        aria-label={openMenuLabel}
      >
        <MenuGlyph />
        <span className="text-sm font-semibold">{shellLabel}</span>
        <span className="max-w-[9rem] truncate border-l border-border pl-2 text-xs font-medium text-muted-foreground">
          {activeLabel}
        </span>
      </button>
    </div>
  )
}

export function WorkspaceLayout({ children }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const { pages, appName } = useNavigation()
  const { user } = useChatUI()
  const userRoles = useMemo(() => getUserRoles(user), [user])
  const appId = resolveAppId(location.pathname)
  const shellLabel = resolveShellLabel(appName, appId)
  const shellSubtext = resolveShellSubtext(appId)
  const navGroups = useMemo(() => buildNavGroupsFromPages(pages, appId, userRoles), [pages, appId, userRoles])
  const activeNav = useMemo(() => getActiveNavItem(navGroups, location), [navGroups, location])
  const activeLabel = activeNav.item?.label || activeNav.group?.label || 'Studio'

  return (
    <div className="min-h-full flex-1 bg-background">
      <div className="mx-auto flex w-full max-w-[96rem] gap-6 px-4 py-7 md:px-6 lg:px-8">
        <div className="hidden w-72 shrink-0 lg:block">
          <div className="sticky top-24">
            <WorkspaceSidebar
              appId={appId}
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
              <WorkspaceSidebar
                appId={appId}
                navGroups={navGroups}
                onNavigate={() => setMobileOpen(false)}
                surface="sheet"
                shellLabel={shellLabel}
                shellSubtext={shellSubtext}
              />
            </div>
          </div>
        ) : null}

        <div className="min-w-0 flex-1 space-y-5 pb-24 md:pb-10 lg:pb-0">
          {children}
        </div>
        <WorkspaceMobileNavTrigger
          onOpenMenu={() => setMobileOpen(true)}
          activeLabel={activeLabel}
          shellLabel={shellLabel}
        />
      </div>
    </div>
  )
}

export default WorkspaceLayout
