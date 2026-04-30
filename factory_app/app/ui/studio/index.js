/**
 * Studio UI registration.
 *
 * Studio is the shared management interface. It remains an optional
 * framework-owned capability loaded by the host shell, but its React
 * implementation now lives inside the factory app UI tree.
 */

import { lazy } from 'react';

const AdminPage = lazy(() => import('@mozaiks/chat-ui/pages/AdminPage.jsx'));
const StudioPage = lazy(() => import('./StudioPage.jsx'));
const StudioHomePage = lazy(() => import('./pages/StudioHomePage.jsx'));
const StudioCreatePage = lazy(() => import('../pages/custom/StudioCreatePage.jsx'));
const StudioAdaptersPage = lazy(() => import('./pages/StudioAdaptersPage.jsx'));

export function registerStudioComponents(registerComponent) {
  if (typeof registerComponent !== 'function') return;

  registerComponent('AdminPortal', AdminPage, {
    description: 'Unified admin shell — app, module, and runtime/operator panels. Platform-management surface registered by Studio.',
  });

  registerComponent('StudioPage', StudioPage, {
    description: 'Studio shell — internal router for all /studio/* paths.',
  });

  registerComponent('StudioHomePage', StudioHomePage, {
    description: 'Studio Home surface — shows app intent, workspace readiness, and the next recommended build step.',
  });

  registerComponent('StudioCreatePage', StudioCreatePage, {
    description: 'Studio Create surface — factory-owned create and refinement control plane routed through the Studio shell.',
  });

  registerComponent('StudioAdaptersPage', StudioAdaptersPage, {
    description: 'Studio Adapters surface — shows API key and adapter configuration status for the runtime.',
  });
}

export function register(registerComponent) {
  registerStudioComponents(registerComponent);
}