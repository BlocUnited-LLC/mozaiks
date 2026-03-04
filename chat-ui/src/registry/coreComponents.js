/**
 * Core Components Registration
 *
 * Registers core chat-ui components in the component registry.
 * These components are available by default and can be referenced in navigation.json.
 *
 * @module @mozaiks/chat-ui/registry/coreComponents
 */

import { registerComponent } from './componentRegistry';

// Core pages
import ChatPage from '../pages/ChatPage';
import DashboardPage from '../pages/DashboardPage';

// Register core components
registerComponent('ChatPage', ChatPage, {
  core: true,
  description: 'Main chat interface page'
});

registerComponent('DashboardPage', DashboardPage, {
  core: true,
  description: 'Admin portal and user dashboard'
});

// Alias: navigation.json can reference either name
registerComponent('AdminPortal', DashboardPage, {
  core: true,
  description: 'Admin portal (alias for DashboardPage)'
});

export const CORE_COMPONENTS = ['ChatPage', 'DashboardPage', 'AdminPortal'];

console.log('[CoreComponents] Registered core chat-ui components:', CORE_COMPONENTS);
