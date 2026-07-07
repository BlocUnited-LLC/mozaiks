/**
 * factory_app/app/admin — admin portal UI registration.
 *
 * All Studio and admin portal pages are co-located here, separate from
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
const StudioPage        = lazy(() => import('./pages/StudioPage.jsx'))
const AppsPage           = lazy(() => import('./pages/AppsPage.jsx'))
const WorkspaceUsagePage = lazy(() => import('./pages/WorkspaceUsagePage.jsx'))
const WorkspaceHealthPage  = lazy(() => import('./pages/WorkspaceHealthPage.jsx'))
const AppOverviewPage    = lazy(() => import('./pages/AppOverviewPage.jsx'))
const AppHealthPage      = lazy(() => import('./pages/AppHealthPage.jsx'))
const AppUsersPage       = lazy(() => import('./pages/AppUsersPage.jsx'))
const AppUsagePage       = lazy(() => import('./pages/AppUsagePage.jsx'))
const AppBuildHistoryPage = lazy(() => import('./pages/AppBuildHistoryPage.jsx'))
const AppIntegrationsPage = lazy(() => import('./pages/AppIntegrationsPage.jsx'))
const AppCommunityPage    = lazy(() => import('./pages/AppCommunityPage.jsx'))
const AppGovernancePage   = lazy(() => import('./pages/AppGovernancePage.jsx'))
const AppGovernanceProposalPage = lazy(() => import('./pages/AppGovernanceProposalPage.jsx'))
const AppCollaboratorsPage = lazy(() => import('./pages/AppCollaboratorsPage.jsx'))
const AppRevenueParticipationPage = lazy(() => import('./pages/AppRevenueParticipationPage.jsx'))
const RevenueParticipationPlanReviewPage = lazy(() => import('./pages/RevenueParticipationPlanReviewPage.jsx'))
const RevenueDistributionReviewPage = lazy(() => import('./pages/RevenueDistributionReviewPage.jsx'))
const MyCommunitiesPage   = lazy(() => import('./pages/MyCommunitiesPage.jsx'))
const MyInvitationsPage   = lazy(() => import('./pages/MyInvitationsPage.jsx'))
const MyVotesPage         = lazy(() => import('./pages/MyVotesPage.jsx'))
const MyDelegationsPage   = lazy(() => import('./pages/MyDelegationsPage.jsx'))

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

  registerComponent('WorkspaceHealthPage', WorkspaceHealthPage, {
    description: 'Workspace health surface — cross-app runtime, workflow, and hosting health in one portfolio view.',
  })

  registerComponent('AppOverviewPage', AppOverviewPage, {
    description: 'App overview surface — shows app intent, readiness, and the next recommended build step.',
    override: true,
  })

  registerComponent('AppHealthPage', AppHealthPage, {
    description: 'App health surface — overall app health across workflows, hosting, integrations, and runtime posture.',
    override: true,
  })

  registerComponent('AppUsersPage', AppUsersPage, {
    description: 'App users surface — user access, operators, and app-scoped participation controls.',
  })

  registerComponent('AppUsagePage', AppUsagePage, {
    description: 'App usage surface — app-scoped usage, adoption, and metering visibility.',
  })

  registerComponent('AppIntegrationsPage', AppIntegrationsPage, {
    description: 'Integrations surface — focused third-party connector inventory and CRUD controls for app-scoped integrations.',
  })

  registerComponent('AppBuildHistoryPage', AppBuildHistoryPage, {
    description: 'Build history surface — artifact versions with carry-forward preservation audit per revision.',
  })

  registerComponent('AppCommunityPage', AppCommunityPage, {
    description: 'App community surface — app-scoped community overview, member roster, and create-first-community flow.',
  })

  registerComponent('AppGovernancePage', AppGovernancePage, {
    description: 'App governance surface — proposal list, create/open/close/outcome actions for app-scoped community governance.',
  })

  registerComponent('AppGovernanceProposalPage', AppGovernanceProposalPage, {
    description: 'Governance proposal detail — vote casting, delegation management, and immutable snapshot review for a single proposal.',
  })

  registerComponent('AppCollaboratorsPage', AppCollaboratorsPage, {
    description: 'App collaborators surface — invite collaborators, update roles, and manage member roster for app-scoped communities.',
  })

  registerComponent('AppRevenueParticipationPage', AppRevenueParticipationPage, {
    description: 'Revenue participation surface — lists workflow-generated plan proposals and distribution reviews for human operator review.',
  })

  registerComponent('RevenueParticipationPlanReviewPage', RevenueParticipationPlanReviewPage, {
    description: 'Revenue plan proposal review — operator reviews a RevenueParticipationDesignerWorkflow artifact and confirms draft plan creation.',
  })

  registerComponent('RevenueDistributionReviewPage', RevenueDistributionReviewPage, {
    description: 'Revenue distribution review — operator reviews a RevenueDistributionReviewWorkflow artifact and confirms settlement period actions.',
  })

  registerComponent('MyCommunitiesPage', MyCommunitiesPage, {
    description: 'My communities surface — shows all community memberships for the current user across all apps.',
  })

  registerComponent('MyInvitationsPage', MyInvitationsPage, {
    description: 'My invitations surface — pending and historical community invitations, with token-based accept/decline flow.',
  })

  registerComponent('MyVotesPage', MyVotesPage, {
    description: 'My votes surface — open governance proposals across the user\'s communities, with navigation to vote on each.',
  })

  registerComponent('MyDelegationsPage', MyDelegationsPage, {
    description: 'My delegations surface — outgoing and incoming voting delegations, with revoke action for active outgoing delegations.',
  })
}
