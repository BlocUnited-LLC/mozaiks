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
import AdminPortal from '../pages/AdminPortal';

// Register core components
registerComponent('ChatPage', ChatPage, {
  core: true,
  description: 'Main chat interface page'
});

registerComponent('AdminPortal', AdminPortal, {
  core: true,
  description: 'Admin portal — account management, usage, admin features'
});

export const CORE_COMPONENTS = ['ChatPage', 'AdminPortal'];

console.log('[CoreComponents] Registered core chat-ui components:', CORE_COMPONENTS);
