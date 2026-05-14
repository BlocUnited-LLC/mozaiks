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
 * UsersSection and UsageSection when the framework mounts those panels.
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
// Section routing
// ---------------------------------------------------------------------------

function AdminSectionRoute(pathname) {
  const suffixMatch = /^\/apps\/[^/]+\/([^/]+)\/?$/.exec(pathname)
  if (!suffixMatch) return 'overview'
  return normalizeSection(suffixMatch[1], 'overview')
}

// ---------------------------------------------------------------------------
// Panel normalization
// ---------------------------------------------------------------------------

const KNOWN_SECTIONS = new Set([
  'overview', 'users', 'billing', 'usage', 'activity',
  'operations', 'settings', 'integrations', 'support',
])

function normalizeSection(value, fallback) {
  const raw = String(value || '').trim().toLowerCase().replace(/_/g, '-')
  if (!raw) return fallback
  if (KNOWN_SECTIONS.has(raw)) return raw
  return fallback
}

function inferSection(panel) {
  const text = [panel?.section, panel?.category, panel?.group, panel?.id, panel?.label, panel?.description]
    .filter(Boolean).join(' ').toLowerCase()
  if (/(user|role|permission|auth|account|member)/.test(text)) return 'users'
  if (/(billing|subscription|invoice|payment|stripe|revenue|plan)/.test(text)) return 'overview'
  if (/(run|session|workflow|cost|token|usage)/.test(text)) return 'usage'
  if (/(operations|activity|audit|log|event|history|incident|alert|health|error|performance)/.test(text)) return 'operations'
  if (/(setting|config|domain|brand|theme|environment)/.test(text)) return 'settings'
  if (/(support|ticket|notification|alert)/.test(text)) return 'operations'
  return 'overview'
}

function getPanelId(panelConfig) {
  return typeof panelConfig === 'string' ? panelConfig : panelConfig?.id
}

function normalizeRuntimePanels(runtimePanels) {
  const normalize = (p) => {
    const id = getPanelId(p)
    if (!id) return null
    const fallback = id === 'sessions' ? 'operations' : 'usage'
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

  const SECTION_LABELS = {
    billing: 'Billing', activity: 'Activity',
    integrations: 'Integrations', support: 'Support',
  }

  let content
  switch (activeSection) {
    case 'users':
      content = <UsersSection section="users" extensionPanels={extensionPanels} />
      break
    case 'usage':
      content = <UsageSection title="Usage" runtimePanels={runtimePanels} extensionPanels={extensionPanels} />
      break
    case 'operations':
      content = <OperationsSection runtimePanels={runtimePanels} extensionPanels={extensionPanels} />
      break
    case 'settings':
      content = <SettingsSection extensionPanels={extensionPanels} />
      break
    case 'billing':
    case 'activity':
    case 'integrations':
    case 'support':
      content = (
        <div className="space-y-4">
          <h1 className="text-lg font-semibold text-foreground">
            {SECTION_LABELS[activeSection]}
          </h1>
          <AdminExtensionPanels panels={extensionPanels} />
        </div>
      )
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
