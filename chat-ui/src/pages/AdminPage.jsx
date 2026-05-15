/**
 * AdminPage — unified admin shell.
 *
 * Owns: role guard, config fetch, page routing, panel rendering.
 * Each built-in page delegates to its own component in src/admin/pages/.
 * Extension pages (declared only in admin_registry.yaml) render AdminExtensionPanels.
 *
 * Access is gated by the "admin" role (client-side guard; backend enforces
 * independently on all /api/admin/* routes).
 *
 * Config comes from GET /api/admin/config:
 *   { "pages": [...], "runtime_panels": [...], "module_panels": [...] }
 *
 * Pages are declared in app/admin/admin_registry.yaml.
 * Module panels reference a page id via the `page` field in admin.yaml.
 */

import { useLocation } from 'react-router-dom'
import { useChatUI } from '../context/ChatUIContext'
import { AdminExtensionPanels, useAdminFetch } from '../admin/components/AdminPrimitives.jsx'
import { AdminWorkspaceLayout } from '../admin/components/AdminWorkspaceLayout.jsx'
import { OverviewSection as AdminOverviewPanel } from '../admin/pages/OverviewSection.jsx'
import { UsersSection }        from '../admin/pages/UsersSection.jsx'
import { UsageSection }        from '../admin/pages/UsageSection.jsx'
import { OperationsSection }   from '../admin/pages/ActivitySection.jsx'
import { SettingsSection }     from '../admin/pages/SettingsSection.jsx'

// ---------------------------------------------------------------------------
// Page routing — extract active page id from the URL suffix
// ---------------------------------------------------------------------------

function AdminSectionRoute(pathname) {
  const suffixMatch = /^\/apps\/[^/]+\/([^/]+)\/?$/.exec(pathname)
  if (!suffixMatch) return 'overview'
  return suffixMatch[1].toLowerCase()
}

// ---------------------------------------------------------------------------
// Built-in page components — pages with dedicated section implementations
// ---------------------------------------------------------------------------

const BUILTIN_PAGES = {
  users:      (props) => <UsersSection {...props} />,
  usage:      (props) => <UsageSection title="Usage" {...props} />,
  operations: (props) => <OperationsSection {...props} />,
  settings:   (props) => <SettingsSection {...props} />,
}

// ---------------------------------------------------------------------------
// Panel utilities
// ---------------------------------------------------------------------------

function normalizeRuntimePanels(runtimePanels) {
  const panels = Array.isArray(runtimePanels) ? runtimePanels : [
    { id: 'stats', page: 'usage' },
    { id: 'runs', page: 'usage' },
    { id: 'sessions', page: 'operations' },
  ]
  return panels.map((p) => ({
    ...p,
    id: typeof p === 'string' ? p : p?.id,
    page: typeof p === 'string' ? (p === 'sessions' ? 'operations' : 'usage') : (p?.page || 'usage'),
    source: 'runtime',
  })).filter((p) => p.id)
}

function normalizeModulePanels(modulePanels) {
  if (!Array.isArray(modulePanels)) return []
  return modulePanels.map((p) => ({ ...p, page: p?.page || 'overview' }))
}

function pagePanels(panels, pageId) {
  return panels.filter((p) => p.page === pageId)
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export default function AdminPage() {
  const { user } = useChatUI()
  const location = useLocation()
  const { data: config } = useAdminFetch('/api/admin/config')

  // Client-side role guard (backend enforces independently on all API routes)
  const isAdmin = user?.roles?.includes('admin') ?? true // true fallback = no-auth dev mode
  if (!isAdmin) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-8 text-center max-w-sm">
          <h1 className="text-lg font-bold text-destructive mb-2">Access Denied</h1>
          <p className="text-sm text-muted-foreground">Admin role required.</p>
        </div>
      </div>
    )
  }

  const activePage        = AdminSectionRoute(location.pathname)
  const allPages          = config?.pages ?? []
  const allRuntimePanels  = normalizeRuntimePanels(config?.runtime_panels)
  const allModulePanels   = normalizeModulePanels(config?.module_panels)
  const runtimePanels     = pagePanels(allRuntimePanels, activePage)
  const extensionPanels   = pagePanels(allModulePanels, activePage)

  let content
  const BuiltinPage = BUILTIN_PAGES[activePage]
  if (activePage === 'overview') {
    content = (
      <AdminOverviewPanel
        runtimePanels={allRuntimePanels}
        extensionPanels={allModulePanels}
      />
    )
  } else if (BuiltinPage) {
    content = <BuiltinPage runtimePanels={runtimePanels} extensionPanels={extensionPanels} />
  } else {
    // Extension-only page — declared in admin_registry.yaml, panels from admin.yaml
    const pageLabel = allPages.find((p) => p.id === activePage)?.label || activePage
    content = (
      <div className="space-y-4">
        <h1 className="text-lg font-semibold text-foreground capitalize">{pageLabel}</h1>
        <AdminExtensionPanels panels={extensionPanels} />
      </div>
    )
  }

  return (
    <AdminWorkspaceLayout adminPages={allPages}>
      <div className="space-y-6">{content}</div>
    </AdminWorkspaceLayout>
  )
}
