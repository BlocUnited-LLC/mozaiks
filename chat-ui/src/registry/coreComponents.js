/**
 * Core Components Registration
 *
 * Registers core chat-ui components in the component registry.
 * These components are available by default and can be referenced in navigation.json.
 *
 * @module @mozaiks/chat-ui/registry/coreComponents
 */

import { registerComponent } from './componentRegistry';

// Core pages shipped with chat-ui.
// AdminPortal remains a platform module registered via @modules auto-discovery.
import ChatPage from '../pages/ChatPage';
import DiscoverPage from '../pages/DiscoverPage';

registerComponent('ChatPage', ChatPage, {
  core: true,
  description: 'Main chat interface page',
});

registerComponent('DiscoverPage', DiscoverPage, {
  core: true,
  description: 'Discovery launcher page',
});

export const CORE_COMPONENTS = ['ChatPage', 'DiscoverPage'];

console.log('[CoreComponents] Registered core chat-ui components:', CORE_COMPONENTS);
