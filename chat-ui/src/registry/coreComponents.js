/**
 * Core Components Registration
 *
 * Registers ONLY core shell components — workflow-agnostic primitives that
 * the shell needs regardless of which platform is loaded.
 *
 * Platform-specific workflow tools are registered automatically via
 * @chat-workflows/index.js, which discovers any
 * platform/workflows/<name>/ui/index.js barrel at build time.
 *
 * Transition UI is shell-owned. Register reusable transition screens here or in
 * a shell component barrel, then reference them with transition.ui.component.
 *
 * @module @mozaiks/chat-ui/registry/coreComponents
 */

import { registerComponent } from './componentRegistry';

// Shell pages
import ChatPage from '../pages/ChatPage';
import { SchemaPage } from '../ui/screens/SchemaPage.jsx';
import AdminPage from '../pages/AdminPage.jsx';
import ProfilePage from '../pages/ProfilePage.jsx';
import AppAdminDashboard from '../pages/AppAdminDashboard.jsx';

// Transition renderers — referenced by transition.ui.component
import { LauncherScreen } from '../ui/screens/LauncherScreen.jsx';
import { ConfirmScreen } from '../ui/screens/ConfirmScreen.jsx';

registerComponent('ChatPage', ChatPage, {
  core: true,
  description: 'Main chat interface page',
});

registerComponent('SchemaPage', SchemaPage, {
  core: true,
  description: 'Renders a declarative AppPageSchema fetched from /api/pages/{name}',
});

registerComponent('LauncherScreen', LauncherScreen, {
  core: true,
  description: 'Default renderer for user_choice transitions. Receives { transition, onResolve } props.',
});

registerComponent('ConfirmScreen', ConfirmScreen, {
  core: true,
  description: 'Built-in renderer for confirm transitions. Receives { transition, onResolve } props.',
});

registerComponent('AdminPortal', AdminPage, {
  core: true,
  description: 'First-class admin dashboard — runtime stats, active runs, session history',
});

registerComponent('ProfilePage', ProfilePage, {
  core: true,
  description: 'First-class user profile page — calls app_backend_url/api/me. Show when auth is enabled.',
});

registerComponent('AppAdminDashboard', AppAdminDashboard, {
  core: true,
  description: 'First-class app admin dashboard — manage users and view stats via app_backend_url/api/admin/*. Always available; gated by admin role.',
});

export const CORE_COMPONENTS = ['ChatPage', 'SchemaPage', 'LauncherScreen', 'ConfirmScreen', 'AdminPortal', 'ProfilePage', 'AppAdminDashboard'];

console.log('[CoreComponents] Registered core chat-ui components:', CORE_COMPONENTS);
