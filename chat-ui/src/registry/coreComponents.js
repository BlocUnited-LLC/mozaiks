/**
 * Core Components Registration
 *
 * Registers ONLY core shell components — workflow-agnostic primitives that
 * the shell needs regardless of which platform is loaded.
 *
 * Platform-specific workflow tools are registered automatically via
 * chat-ui's workflow registry module, which reads ui/index.js barrels from the
 * host-injected workflow root at build time.
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
import ProfilePage from '../pages/ProfilePage.jsx';

// Transition renderers — referenced by transition.ui.component
import { LauncherScreen } from '../ui/screens/LauncherScreen.jsx';
import { ConfirmScreen } from '../ui/screens/ConfirmScreen.jsx';
import WorkflowCompletion from '../components/chat/WorkflowCompletion.jsx';

// Token usage surfaces — profile tab + admin panels
import TokenStatusTab from '../components/profile/TokenStatusTab.jsx';
import AdminMyUsagePanel from '../admin/components/AdminMyUsagePanel.jsx';
import AdminAppUsagePanel from '../admin/components/AdminAppUsagePanel.jsx';

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

registerComponent('ProfilePage', ProfilePage, {
  core: true,
  description: 'Host-owned account page — calls /api/me and /api/me/preferences.',
});

registerComponent('WorkflowCompletion', WorkflowCompletion, {
  core: true,
  description: 'Terminal transition screen shown when a workflow run completes with no server transition pending. Receives { transition, onResolve } from TransitionScreen.',
});

registerComponent('TokenStatusTab', TokenStatusTab, {
  core: true,
  description: 'Profile tab showing the signed-in user\'s AI token balance and usage bar. Receives { tab, data } from ProfilePage. Data shape: { wallets: [{ wallet_id, label, unit, balance, plan_allowances }] }.',
});

registerComponent('AdminMyUsagePanel', AdminMyUsagePanel, {
  core: true,
  description: 'Admin portal panel showing the signed-in admin\'s personal AI token balance. Fetches /api/me/tokens. Receives { panel, apiBaseUrl, auth }.',
});

registerComponent('AdminAppUsagePanel', AdminAppUsagePanel, {
  core: true,
  description: 'Admin portal panel showing aggregate AI token consumption across all app users. Fetches /api/admin/usage. Receives { panel, apiBaseUrl, auth }.',
});

export const CORE_COMPONENTS = ['ChatPage', 'SchemaPage', 'LauncherScreen', 'ConfirmScreen', 'ProfilePage', 'WorkflowCompletion', 'TokenStatusTab', 'AdminMyUsagePanel', 'AdminAppUsagePanel'];

