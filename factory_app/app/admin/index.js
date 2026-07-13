/**
 * factory_app/app/admin — admin portal UI registration.
 *
 * All Studio and admin portal pages are co-located here, separate from
 * user-facing app pages in app/ui/. First-party Studio routes are declared
 * in app/ui/route_manifest.json. admin/admin_registry.yaml is reserved for
 * AdminPortal extension pages. This barrel registers the custom page
 * components that back both surfaces.
 *
 * Generated apps: admin pages for a generated app belong in admin/pages/
 * of that app's workspace, registered through its own admin/index.js.
 */

import { lazy } from 'react'

const AdminPage          = lazy(() => import('@mozaiks/chat-ui/pages/AdminPage.jsx'))
const StudioPage        = lazy(() => import('./pages/StudioPage.jsx'))
const AppsPage           = lazy(() => import('./pages/AppsPage.jsx'))
const WorkspaceUsagePage         = lazy(() => import('./pages/WorkspaceUsagePage.jsx'))
const WorkspaceIntegrationsPage  = lazy(() => import('./pages/WorkspaceIntegrationsPage.jsx'))
const AppOverviewPage    = lazy(() => import('./pages/AppOverviewPage.jsx'))
const AppHealthPage      = lazy(() => import('./pages/AppHealthPage.jsx'))
const AppAccessPage      = lazy(() => import('./pages/AppAccessPage.jsx'))
const AppUsagePage       = lazy(() => import('./pages/AppUsagePage.jsx'))
const AppBuildHistoryPage = lazy(() => import('./pages/AppBuildHistoryPage.jsx'))
const AppIntegrationsPage = lazy(() => import('./pages/AppIntegrationsPage.jsx'))
const AppSupportPage      = lazy(() => import('./pages/AppSupportPage.jsx'))
const ProfilePage         = lazy(() => import('@mozaiks/chat-ui/pages/ProfilePage.jsx'))

export function registerAdminComponents(registerComponent) {
  if (typeof registerComponent !== 'function') return

  registerComponent('AdminPortal', AdminPage, {
    description: 'Unified admin shell — app, module, and runtime/operator panels. Platform-management surface registered by Studio.',
  })

  registerComponent('StudioPage', StudioPage, {
    description: 'Studio shell — internal router for the first-party app directory and app management surfaces.',
  })

  registerComponent('AppsPage', AppsPage, {
    description: 'Apps directory — shows app records for the current user and routes into app Studio per app.',
  })

  registerComponent('WorkspaceUsagePage', WorkspaceUsagePage, {
    description: 'Workspace usage surface — portfolio-level usage, capacity, and value trends across all apps.',
  })

  registerComponent('WorkspaceIntegrationsPage', WorkspaceIntegrationsPage, {
    description: 'Workspace integrations surface — catalog of third-party services with live env-derived configuration status and operator notes.',
  })

  registerComponent('AppOverviewPage', AppOverviewPage, {
    description: 'App overview surface — shows app intent, readiness, and the next recommended build step.',
    override: true,
  })

  registerComponent('AppHealthPage', AppHealthPage, {
    description: 'App health surface — overall app health across workflows, hosting, integrations, and runtime posture.',
    override: true,
  })

  registerComponent('AppAccessPage', AppAccessPage, {
    description: 'App access surface — account access, roles, permissions, plan assignment context, and support-access posture.',
  })

  registerComponent('AppUsagePage', AppUsagePage, {
    description: 'App usage surface — app-scoped usage, adoption, and metering visibility.',
  })

  registerComponent('AppIntegrationsPage', AppIntegrationsPage, {
    description: 'App integration setup detail — app-declared service requirements with workspace-managed provider status.',
  })

  registerComponent('AppSupportPage', AppSupportPage, {
    description: 'App support surface — help desk notes, escalations, stalled runs, and support-facing diagnostics.',
  })

  registerComponent('AppBuildHistoryPage', AppBuildHistoryPage, {
    description: 'Build history surface — artifact versions with carry-forward preservation audit per revision.',
  })

  registerComponent('ProfilePage', ProfilePage, {
    description: 'User profile surface — account identity, avatar, bio, and profile preferences.',
  })
}
