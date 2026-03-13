/**
 * ChatPage extracted hooks for cleaner code organization.
 *
 * These hooks encapsulate specific responsibilities from the original
 * monolithic ChatPage component:
 *
 * - useConversation: Message state, streaming, history
 * - useArtifacts: Artifact display, caching, updates
 * - useChatWebSocket: WebSocket connection management
 */

export { useConversation } from './useConversation';
export { useArtifacts } from './useArtifacts';
export { useChatWebSocket } from './useChatWebSocket';
