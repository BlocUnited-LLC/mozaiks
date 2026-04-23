/**
 * Optional Studio UI registration.
 *
 * Studio is the local builder surface used by the CLI and reused by the hosted
 * Mozaiks product. It is not a core shell component for every app.
 */

import React from 'react';

// StudioPage is the single shell entry point — it handles all /studio/* sub-routing
// internally so the shell config only needs /studio + /studio/* wildcard entries.
const StudioPage = React.lazy(() => import('./StudioPage.jsx'));

// Individual page registrations retained for direct reference / testing.
const StudioHomePage = React.lazy(() => import('./pages/StudioHomePage.jsx'));
const StudioBuildPage = React.lazy(() => import('./pages/StudioBuildPage.jsx'));
const StudioAdaptersPage = React.lazy(() => import('./pages/StudioAdaptersPage.jsx'));

export function registerStudioComponents(registerComponent) {
  if (typeof registerComponent !== 'function') return;

  registerComponent('StudioPage', StudioPage, {
    description: 'Studio shell — internal router for all /studio/* paths.',
  });

  registerComponent('StudioHomePage', StudioHomePage, {
    description: 'Studio Home surface — shows app intent, workspace readiness, and the next recommended build step.',
  });

  registerComponent('StudioBuildPage', StudioBuildPage, {
    description: 'Studio Build surface — drafts build requests and routes into the right workflow path for the current workspace.',
  });

  registerComponent('StudioAdaptersPage', StudioAdaptersPage, {
    description: 'Studio Adapters surface — shows API key and adapter configuration status for the runtime.',
  });
}

export function register(registerComponent) {
  registerStudioComponents(registerComponent);
}
