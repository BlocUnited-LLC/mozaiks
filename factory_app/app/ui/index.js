/**
 * factory_app/app/ui — Studio UI registration.
 *
 * Studio is the shared management interface. It remains an optional
 * framework-owned capability loaded by the host shell, but its React
 * implementation lives inside the factory app UI tree under pages/custom/studio/.
 */

import { lazy } from 'react';

const AdminPage = lazy(() => import('@mozaiks/chat-ui/pages/AdminPage.jsx'));
const StudioPage = lazy(() => import('./pages/custom/studio/StudioPage.jsx'));
const HubPage = lazy(() => import('./pages/custom/studio/HubPage.jsx'));
const StudioHomePage = lazy(() => import('./pages/custom/studio/StudioHomePage.jsx'));
const StudioCreatePage = lazy(() => import('./pages/custom/StudioCreatePage.jsx'));
const StudioAdaptersPage = lazy(() => import('./pages/custom/studio/StudioAdaptersPage.jsx'));

export function registerStudioComponents(registerComponent) {
  if (typeof registerComponent !== 'function') return;

  registerComponent('AdminPortal', AdminPage, {
    description: 'Unified admin shell — app, module, and runtime/operator panels. Platform-management surface registered by Studio.',
  });

  registerComponent('StudioPage', StudioPage, {
    description: 'Studio shell — internal router for all /studio/* and /hub paths.',
  });

  registerComponent('HubPage', HubPage, {
    description: 'Hub — My Apps list. Shows all app records for the current user and routes into Studio per app.',
  });

  registerComponent('StudioHomePage', StudioHomePage, {
    description: 'Studio Home surface — shows app intent, workspace readiness, and the next recommended build step.',
  });

  registerComponent('StudioCreatePage', StudioCreatePage, {
    description: 'Studio Create surface — factory-owned create and refinement control plane routed through the Studio shell.',
  });

  registerComponent('StudioAdaptersPage', StudioAdaptersPage, {
    description: 'Studio Adapters surface — focused third-party adapter inventory and CRUD controls for app-scoped integrations.',
  });
}

export function register(registerComponent) {
  registerStudioComponents(registerComponent);
}
