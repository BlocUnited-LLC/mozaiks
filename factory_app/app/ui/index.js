/**
 * factory_app/app/ui — first-party workspace UI registration.
 *
 * The first-party workspace console remains a framework-owned capability loaded
 * by the host shell, but its React implementation lives inside the factory app
 * UI tree under pages/custom/console/.
 */

import { lazy } from 'react';

const AdminPage = lazy(() => import('@mozaiks/chat-ui/pages/AdminPage.jsx'));
const ConsolePage = lazy(() => import('./pages/custom/console/ConsolePage.jsx'));
const AppsPage = lazy(() => import('./pages/custom/console/AppsPage.jsx'));
const WorkspaceUsagePage = lazy(() => import('./pages/custom/console/WorkspaceUsagePage.jsx'));
const WorkspaceBillingPage = lazy(() => import('./pages/custom/console/WorkspaceBillingPage.jsx'));
const WorkspaceHostingPage = lazy(() => import('./pages/custom/console/WorkspaceHostingPage.jsx'));
const AppOverviewPage = lazy(() => import('./pages/custom/console/AppOverviewPage.jsx'));
const AppUsersPage = lazy(() => import('./pages/custom/console/AppUsersPage.jsx'));
const AppUsagePage = lazy(() => import('./pages/custom/console/AppUsagePage.jsx'));
const AppBillingPage = lazy(() => import('./pages/custom/console/AppBillingPage.jsx'));
const AppIntegrationsPage = lazy(() => import('./pages/custom/console/AppIntegrationsPage.jsx'));
const AppHostingPage = lazy(() => import('./pages/custom/console/AppHostingPage.jsx'));

export function registerConsoleComponents(registerComponent) {
  if (typeof registerComponent !== 'function') return;

  registerComponent('AdminPortal', AdminPage, {
    description: 'Unified admin shell — app, module, and runtime/operator panels. Platform-management surface registered by the workspace console.',
  });

  registerComponent('ConsolePage', ConsolePage, {
    description: 'Workspace console shell — internal router for the first-party app directory and app-console surfaces.',
  });

  registerComponent('AppsPage', AppsPage, {
    description: 'Apps directory — shows app records for the current user and routes into the app console per app.',
  });

  registerComponent('WorkspaceUsagePage', WorkspaceUsagePage, {
    description: 'Workspace usage surface — portfolio-level usage, capacity, and value trends across all apps.',
  });

  registerComponent('WorkspaceBillingPage', WorkspaceBillingPage, {
    description: 'Workspace billing surface — billing ownership, revenue posture, and commercial readiness for all apps.',
  });

  registerComponent('WorkspaceHostingPage', WorkspaceHostingPage, {
    description: 'Workspace hosting surface — managed hosting posture, release readiness, and production attention across all apps.',
  });

  registerComponent('AppOverviewPage', AppOverviewPage, {
    description: 'App overview surface — shows app intent, readiness, and the next recommended build step.',
  });

  registerComponent('AppUsersPage', AppUsersPage, {
    description: 'App users surface — user access, operators, and app-scoped participation controls.',
  });

  registerComponent('AppUsagePage', AppUsagePage, {
    description: 'App usage surface — app-scoped usage, adoption, and metering visibility.',
  });

  registerComponent('AppBillingPage', AppBillingPage, {
    description: 'App billing surface — app-level billing, customer value, and commercial controls.',
  });

  registerComponent('AppIntegrationsPage', AppIntegrationsPage, {
    description: 'Integrations surface — focused third-party connector inventory and CRUD controls for app-scoped integrations.',
  });

  registerComponent('AppHostingPage', AppHostingPage, {
    description: 'App hosting surface — managed hosting readiness, domains, and production posture for a single app.',
  });
}

export function register(registerComponent) {
  registerConsoleComponents(registerComponent);
}
