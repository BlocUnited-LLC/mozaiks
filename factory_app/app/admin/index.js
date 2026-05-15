/**
 * factory_app/app/admin — admin portal UI registration.
 *
 * All console and admin portal pages are co-located here, separate from
 * user-facing app pages in app/ui/. The admin portal is platform-owned
 * (rendered through AdminPortal / AdminWorkspaceLayout) and declared via
 * admin/admin_registry.yaml. This barrel registers the custom page
 * components that back those routes.
 *
 * Generated apps: admin pages for a generated app belong in admin/pages/
 * of that app's workspace, registered through its own admin/index.js.
 */

import { lazy } from 'react'

const AdminPage          = lazy(() => import('@mozaiks/chat-ui/pages/AdminPage.jsx'))
const ConsolePage        = lazy(() => import('./pages/ConsolePage.jsx'))
const AppsPage           = lazy(() => import('./pages/AppsPage.jsx'))
const WorkspaceUsagePage = lazy(() => import('./pages/WorkspaceUsagePage.jsx'))
const WorkspaceHealthPage  = lazy(() => import('./pages/WorkspaceHealthPage.jsx'))
const WorkspaceBillingPage = lazy(() => import('./pages/WorkspaceBillingPage.jsx'))
const WorkspaceHostingPage = lazy(() => import('./pages/WorkspaceHostingPage.jsx'))
const AppOverviewPage    = lazy(() => import('./pages/AppOverviewPage.jsx'))
const AppHealthPage      = lazy(() => import('./pages/AppHealthPage.jsx'))
const AppUsersPage       = lazy(() => import('./pages/AppUsersPage.jsx'))
const AppUsagePage       = lazy(() => import('./pages/AppUsagePage.jsx'))
const AppBillingPage     = lazy(() => import('./pages/AppBillingPage.jsx'))
const AppIntegrationsPage = lazy(() => import('./pages/AppIntegrationsPage.jsx'))
const AppHostingPage     = lazy(() => import('./pages/AppHostingPage.jsx'))

export function registerAdminComponents(registerComponent) {
  if (typeof registerComponent !== 'function') return

  registerComponent('AdminPortal', AdminPage, {
    description: 'Unified admin shell — app, module, and runtime/operator panels. Platform-management surface registered by the workspace console.',
  })

  registerComponent('ConsolePage', ConsolePage, {
    description: 'Workspace console shell — internal router for the first-party app directory and app-console surfaces.',
  })

  registerComponent('AppsPage', AppsPage, {
    description: 'Apps directory — shows app records for the current user and routes into the app console per app.',
  })

  registerComponent('WorkspaceUsagePage', WorkspaceUsagePage, {
    description: 'Workspace usage surface — portfolio-level usage, capacity, and value trends across all apps.',
  })

  registerComponent('WorkspaceHealthPage', WorkspaceHealthPage, {
    description: 'Workspace health surface — cross-app runtime, workflow, and hosting health in one portfolio view.',
  })

  registerComponent('WorkspaceBillingPage', WorkspaceBillingPage, {
    description: 'Workspace billing surface — billing ownership, revenue posture, and commercial readiness for all apps.',
  })

  registerComponent('WorkspaceHostingPage', WorkspaceHostingPage, {
    description: 'Workspace hosting surface — managed hosting posture, release readiness, and production attention across all apps.',
  })

  registerComponent('AppOverviewPage', AppOverviewPage, {
    description: 'App overview surface — shows app intent, readiness, and the next recommended build step.',
  })

  registerComponent('AppHealthPage', AppHealthPage, {
    description: 'App health surface — overall app health across workflows, hosting, integrations, and runtime posture.',
  })

  registerComponent('AppUsersPage', AppUsersPage, {
    description: 'App users surface — user access, operators, and app-scoped participation controls.',
  })

  registerComponent('AppUsagePage', AppUsagePage, {
    description: 'App usage surface — app-scoped usage, adoption, and metering visibility.',
  })

  registerComponent('AppBillingPage', AppBillingPage, {
    description: 'App billing surface — app-level billing, customer value, and commercial controls.',
  })

  registerComponent('AppIntegrationsPage', AppIntegrationsPage, {
    description: 'Integrations surface — focused third-party connector inventory and CRUD controls for app-scoped integrations.',
  })

  registerComponent('AppHostingPage', AppHostingPage, {
    description: 'App hosting surface — managed hosting readiness, domains, and production posture for a single app.',
  })
}
