/**
 * AdminPage — unified admin shell.
 *
 * Owns: role guard, config fetch, page routing, and one layout for every page:
 *
 *   page title and description
 *   framework content        (only the pages in FRAMEWORK_PAGES)
 *   app-backend panels       (panels whose `section` is this page)
 *   module panels            (panels whose `page` is this page)
 *   empty state              (when the page has none of the above)
 *
 * The server admits each page and module panel using the loaded app grants.
 * Framework data and connected-backend panels remain admin-only.
 *
 * Config comes from GET /api/admin/config:
 *   { "pages": [...], "runtime_panels": [...], "module_panels": [...] }
 *
 * Pages are declared in app/admin/admin_registry.yaml. The standard page ids
 * and their wording live in admin/contracts/adminPages.js.
 */

import { useLocation } from 'react-router-dom'
import { useChatUI } from '../context/ChatUIContext'
import {
  AdminExtensionPanels,
  EmptyState,
  ErrorBox,
  Spinner,
  useAdminFetch,
} from '../admin/components/AdminPrimitives.jsx'
import { AdminWorkspaceLayout } from '../admin/components/AdminWorkspaceLayout.jsx'
import { getStandardAdminPage } from '../admin/contracts/adminPages.js'
import { OverviewSection }   from '../admin/pages/OverviewSection.jsx'
import { UsageSection }      from '../admin/pages/UsageSection.jsx'
import { OperationsSection } from '../admin/pages/OperationsSection.jsx'
import { SettingsSection }   from '../admin/pages/SettingsSection.jsx'
import { AppAdminPanels, useAppBackendAdminPanels } from './AppAdminDashboard.jsx'

// ---------------------------------------------------------------------------
// Page routing — extract active page id from the URL suffix
// ---------------------------------------------------------------------------

function AdminSectionRoute(pathname) {
  const suffixMatch = /^\/apps\/[^/]+\/([^/]+)\/?$/.exec(pathname)
  if (!suffixMatch) return 'overview'
  return suffixMatch[1].toLowerCase()
}

// ---------------------------------------------------------------------------
// Framework content — pages the framework itself has something to show on.
// Every other page is filled only by app-backend and module panels.
// ---------------------------------------------------------------------------

const FRAMEWORK_PAGES = {
  overview:   OverviewSection,
  usage:      UsageSection,
  operations: OperationsSection,
  settings:   SettingsSection,
}

// ---------------------------------------------------------------------------
// Panel utilities
// ---------------------------------------------------------------------------

// Mirrors DEFAULT_RUNTIME_PANELS in mozaiksai/core/admin/contract.py; used only
// until /api/admin/config answers.
const DEFAULT_RUNTIME_PANELS = [
  { id: 'stats', page: 'usage' },
  { id: 'runs', page: 'usage' },
  { id: 'sessions', page: 'operations' },
]

function normalizeRuntimePanels(runtimePanels) {
  const panels = Array.isArray(runtimePanels) ? runtimePanels : DEFAULT_RUNTIME_PANELS
  return panels
    .filter((p) => p && typeof p === 'object' && p.id && p.page)
    .map((p) => ({ ...p, source: 'runtime' }))
}

function normalizeModulePanels(modulePanels) {
  if (!Array.isArray(modulePanels)) return []
  return modulePanels
    .filter((p) => p && typeof p === 'object' && p.id && p.page)
    .slice()
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
}

function pagePanels(panels, pageId) {
  return panels.filter((p) => p.page === pageId)
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export default function AdminPage({ route } = {}) {
  const { user, config } = useChatUI()
  const identity = JSON.stringify([user?.id, user?.user_id, user?.app_id, user?.tenant_id,
    user?.workspace_id, user?.roles, user?.scopes, config?.appId, config?.app_id])
  return <AdminPageContent key={identity} route={route} />
}

function AdminPageContent({ route }) {
  const location = useLocation()
  const { data: config, loading: configLoading, error: configError } = useAdminFetch('/api/admin/config')
  const isAdmin = !configLoading && config?.is_admin === true
  const appBackend = useAppBackendAdminPanels({ enabled: isAdmin })
  const activePage = route?.meta?.adminPage || AdminSectionRoute(location.pathname)
  const allPages = config?.pages ?? []
  const declaredPage = allPages.find((page) => page.id === activePage) || null

  if (configLoading) return <Spinner />
  if (!config || !declaredPage) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-8 text-center max-w-sm">
          <h1 className="text-lg font-bold text-destructive mb-2">Access Denied</h1>
          <p className="text-sm text-muted-foreground">You do not have access to this admin page.</p>
        </div>
      </div>
    )
  }

  const standardPage  = getStandardAdminPage(activePage)
  const title         = declaredPage?.label || standardPage?.label || activePage
  const description   = standardPage?.description || null

  const runtimePanels = pagePanels(normalizeRuntimePanels(config?.runtime_panels), activePage)
  const modulePanels  = pagePanels(normalizeModulePanels(config?.module_panels), activePage)
  const backendPanels = appBackend.panels
    .filter((p) => p.section === activePage)
    .sort((a, b) => a.order - b.order)

  const FrameworkContent = isAdmin ? FRAMEWORK_PAGES[activePage] || null : null
  const stillLoading = (configLoading && !config) || appBackend.loading
  const isEmpty = !FrameworkContent && modulePanels.length === 0 && backendPanels.length === 0

  return (
    <AdminWorkspaceLayout adminPages={allPages}>
      <div className="space-y-6">
        <header>
          <h1 className="text-2xl font-bold text-foreground">{title}</h1>
          {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
        </header>

        {configError && !config ? (
          <ErrorBox message={`Admin configuration unavailable: ${configError}`} />
        ) : null}
        {appBackend.error ? (
          <ErrorBox message={`Panels from the connected backend could not be loaded: ${appBackend.error}`} />
        ) : null}

        {FrameworkContent ? <FrameworkContent runtimePanels={runtimePanels} /> : null}
        <AppAdminPanels panels={backendPanels} backendUrl={appBackend.backendUrl} auth={appBackend.auth} />
        <AdminExtensionPanels panels={modulePanels} />

        {isEmpty && stillLoading ? <Spinner /> : null}
        {isEmpty && !stillLoading && !configError ? (
          <EmptyState>
            Nothing has been added to {title} yet. Features installed in this app show their admin
            tools here.{standardPage?.emptyHint ? ` ${standardPage.emptyHint}` : ''}
          </EmptyState>
        ) : null}
      </div>
    </AdminWorkspaceLayout>
  )
}
