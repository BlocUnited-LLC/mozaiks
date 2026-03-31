/**
 * Core Components Registration
 *
 * Registers core chat-ui components in the component registry.
 * Only the shell-owned ChatPage lives here; app pages come from platform/pages.
 *
 * @module @mozaiks/chat-ui/registry/coreComponents
 */

import { registerComponent } from './componentRegistry';

// ChatPage is the only built-in shell page.
import ChatPage from '../pages/ChatPage';

registerComponent('ChatPage', ChatPage, {
  core: true,
  description: 'Main chat interface page',
});

export const CORE_COMPONENTS = ['ChatPage'];

console.log('[CoreComponents] Registered core chat-ui components:', CORE_COMPONENTS);
