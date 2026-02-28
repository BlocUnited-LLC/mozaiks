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

// Register core components
registerComponent('ChatPage', ChatPage, {
  core: true,
  description: 'Main chat interface page'
});

// Export for potential programmatic access
export const CORE_COMPONENTS = [
  'ChatPage'
];

console.log('[CoreComponents] Registered core chat-ui components:', CORE_COMPONENTS);
