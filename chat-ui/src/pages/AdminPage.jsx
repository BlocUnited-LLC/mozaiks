/**
 * AdminPage — unified admin shell.
 *
 * Owns: role guard, config fetch, panel normalization, section routing.
 * Each section delegates rendering to its own component in src/admin/pages/.
 *
 * Access is gated by the "admin" role (client-side guard here; backend enforces
 * independently on all /api/admin/* routes).
 *
 * Framework-owned sections and runtime panels are driven by the built-in admin shell contract via
 * GET /api/admin/config:
 *   { "sections": { ... }, "runtime_panels": [...], "module_panels": [...] }
 *
 * App-business admin panels may come from a connected app backend's
 * /api/admin/config and are embedded by section-level components such as
 * UsersSection and BillingSection.
 */

import { useLocation } from 'react-router-dom'
import { useChatUI } from '../context/ChatUIContext'
import { AdminExtensionPanels, useAdminFetch } from '../admin/components/AdminPrimitives.jsx'
import { AdminWorkspaceLayout } from '../admin/components/AdminWorkspaceLayout.jsx'
import { OverviewSection as AdminOverviewPanel } from '../admin/pages/OverviewSection.jsx'
import { UsersSection }        from '../admin/pages/UsersSection.jsx'
import { BillingSection }      from '../admin/pages/BillingSection.jsx'
import { UsageSection }        from '../admin/pages/UsageSection.jsx'
import { ActivitySection }     from '../admin/pages/ActivitySection.jsx'
import { SettingsSection }     from '../admin/pages/SettingsSection.jsx'
import { IntegrationsSection } from '../admin/pages/IntegrationsSection.jsx'
import { SupportSection }      from '../admin/pages/SupportSection.jsx'

// ---------------------------------------------------------------------------
// Section routing
// ---------------------------------------------------------------------------

const ADMIN_SECTION_ROUTES = {
  '/admin': 'overview',
  '/admin/users': 'users',
  '/admin/billing': 'billing',
  '/admin/usage': 'usage',
  '/admin/activity': 'activity',
  '/admin/settings': 'settings',
  '/admin/integrations': 'integrations',
  '/admin/support': 'support',
}

function AdminSectionRoute(pathname) {
  return ADMIN_SECTION_ROUTES[pathname] || 'overview'
}

// ---------------------------------------------------------------------------
// Panel normalization
// ---------------------------------------------------------------------------

const KNOWN_SECTIONS = new Set([
  'overview', 'users', 'billing', 'usage', 'activity', 'settings', 'integrations', 'support',
])

function normalizeSection(value, fallback) {
  const raw = String(value || '').trim().toLowerCase().replace(/_/g, '-')
  if (!raw) return fallback
  if (raw === 'user' || raw === 'access' || raw === 'users-access') return 'users'
  if (raw === 'subscription' || raw === 'subscriptions' || raw === 'payments' || raw === 'revenue') return 'billing'
  if (raw === 'runtime' || raw === 'health' || raw === 'usage-health' || raw === 'workflows') return 'usage'
  if (raw === 'audit' || raw === 'logs' || raw === 'events') return 'activity'
  if (raw === 'config' || raw === 'configuration') return 'settings'
  if (raw === 'module' || raw === 'modules' || raw === 'feature' || raw === 'features') return 'integrations'
  if (KNOWN_SECTIONS.has(raw)) return raw
  return fallback
}

function inferSection(panel) {
  const text = [panel?.section, panel?.category, panel?.group, panel?.id, panel?.label, panel?.description]
    .filter(Boolean).join(' ').toLowerCase()
  if (/(user|role|permission|auth|account|member)/.test(text)) return 'users'
  if (/(billing|subscription|invoice|payment|stripe|revenue|plan)/.test(text)) return 'billing'
  if (/(run|session|workflow|usage|health|cost|token|error|performance)/.test(text)) return 'usage'
  if (/(activity|audit|log|event|history)/.test(text)) return 'activity'
  if (/(setting|config|domain|brand|theme|environment)/.test(text)) return 'settings'
  if (/(support|ticket|notification|alert)/.test(text)) return 'support'
  return 'integrations'
}

function getPanelId(panelConfig) {
  return typeof panelConfig === 'string' ? panelConfig : panelConfig?.id
}

function normalizeRuntimePanels(runtimePanels) {
  const normalize = (p) => {
    const id = getPanelId(p)
    if (!id) return null
    const fallback = id === 'sessions' ? 'activity' : 'usage'
    if (typeof p === 'string') return { id: p, section: fallback }
    return { ...p, section: normalizeSection(p?.section || p?.category || p?.group, fallback) }
  }
  const panels = Array.isArray(runtimePanels) ? runtimePanels : ['stats', 'runs', 'sessions']
  return panels.map(normalize).filter(Boolean)
}

function normalizeModulePanels(modulePanels) {
  if (!Array.isArray(modulePanels)) return []
  return modulePanels.map((p) => ({
    ...p,
    section: normalizeSection(p?.section || p?.category || p?.group, inferSection(p)),
  }))
}

function sectionPanels(panels, section) {
  return panels.filter((p) => p.section === section)
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

  const activeSection       = AdminSectionRoute(location.pathname)
  const allRuntimePanels    = normalizeRuntimePanels(config?.runtime_panels)
  const allExtensionPanels  = normalizeModulePanels(config?.module_panels)
  const runtimePanels       = sectionPanels(allRuntimePanels, activeSection)
  const extensionPanels     = sectionPanels(allExtensionPanels, activeSection)

  let content
  switch (activeSection) {
    case 'users':
      content = <UsersSection section="users" extensionPanels={extensionPanels} />
      break
    case 'billing':
      content = <BillingSection section="billing" extensionPanels={extensionPanels} />
      break
    case 'usage':
      content = <UsageSection title="Usage" runtimePanels={runtimePanels} extensionPanels={extensionPanels} />
      break
    case 'activity':
      content = <ActivitySection runtimePanels={runtimePanels} extensionPanels={extensionPanels} />
      break
    case 'settings':
      content = <SettingsSection extensionPanels={extensionPanels} />
      break
    case 'integrations':
      content = (
        <IntegrationsSection
          allExtensionPanels={allExtensionPanels}
          extensionPanels={extensionPanels}
        />
      )
      break
    case 'support':
      content = <SupportSection extensionPanels={extensionPanels} />
      break
    default:
      content = (
        <AdminOverviewPanel
          runtimePanels={allRuntimePanels}
          extensionPanels={allExtensionPanels}
        />
      )
  }

  return (
    <AdminWorkspaceLayout adminSections={config?.sections}>
      <div className="space-y-6">{content}</div>
    </AdminWorkspaceLayout>
  )
}
