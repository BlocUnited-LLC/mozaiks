/**
 * Optional Studio UI registration.
 *
 * Studio is the local builder surface used by the CLI and reused by the hosted
 * Mozaiks product. It is not a core shell component for every app.
 */

import React from 'react';

const StudioHomePage = React.lazy(() => import('./pages/StudioHomePage.jsx'));
const StudioBuildPage = React.lazy(() => import('./pages/StudioBuildPage.jsx'));

export function registerStudioComponents(registerComponent) {
  if (typeof registerComponent !== 'function') return;

  registerComponent('StudioHomePage', StudioHomePage, {
    description: 'Studio Home surface — shows app intent, workspace readiness, and the next recommended build step.',
  });

  registerComponent('StudioBuildPage', StudioBuildPage, {
    description: 'Studio Build surface — drafts build requests and routes into the right workflow path for the current workspace.',
  });
}

export function register(registerComponent) {
  registerStudioComponents(registerComponent);
}
