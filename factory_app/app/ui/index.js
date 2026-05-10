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
const AppOverviewPage = lazy(() => import('./pages/custom/console/AppOverviewPage.jsx'));
const AppBuildPage = lazy(() => import('./pages/custom/console/AppBuildPage.jsx'));
const AppIntegrationsPage = lazy(() => import('./pages/custom/console/AppIntegrationsPage.jsx'));

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

  registerComponent('AppOverviewPage', AppOverviewPage, {
    description: 'App overview surface — shows app intent, readiness, and the next recommended build step.',
  });

  registerComponent('AppBuildPage', AppBuildPage, {
    description: 'Build surface — factory-owned create and refinement surface routed through the workspace console.',
  });

  registerComponent('AppIntegrationsPage', AppIntegrationsPage, {
    description: 'Integrations surface — focused third-party connector inventory and CRUD controls for app-scoped integrations.',
  });
}

export function register(registerComponent) {
  registerConsoleComponents(registerComponent);
}
