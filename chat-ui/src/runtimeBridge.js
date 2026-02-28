/**
 * AI Runtime Bridge for Frontend
 * 
 * This bridges ChatUI with mozaiks-core's auth and state management.
 * It allows the chat components to:
 * - Use core's auth tokens
 * - Access core's user context
 * - Route WebSocket connections correctly
 */

/**
 * Build WebSocket URL for AI runtime chat
 * 
 * @param {Object} config - Configuration object
 * @param {string} config.workflowName - The workflow to connect to
 * @param {string} config.appId - The app context
 * @param {string} config.chatId - The chat session ID
 * @param {string} config.userId - The authenticated user ID
 * @param {string} config.token - JWT access token
 * @param {string} [config.runtimeUrl] - Optional runtime base URL
 * @returns {string} WebSocket URL with auth
 */
export function buildRuntimeWebSocketUrl({
  workflowName,
  appId,
  chatId,
  userId,
  token,
  runtimeUrl = null,
}) {
  // Determine base URL
  // In production, AI runtime might be same-origin or different service
  let baseWsUrl;
  
  if (runtimeUrl) {
    // Explicit runtime URL provided
    const url = new URL(runtimeUrl);
    const protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    baseWsUrl = `${protocol}//${url.host}`;
  } else {
    // Same-origin: use current host with runtime port
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    const port = import.meta.env.VITE_AI_RUNTIME_PORT || '8080';
    baseWsUrl = `${protocol}//${host}:${port}`;
  }
  
  // Build the runtime WebSocket path
  // Format: /ws/{workflow_name}/{app_id}/{chat_id}/{user_id}
  const path = `/ws/${workflowName}/${appId}/${chatId}/${userId}`;
  
  // Add token as query param
  const url = new URL(path, baseWsUrl);
  if (token) {
    url.searchParams.set('access_token', token);
  }
  
  return url.toString();
}

/**
 * Build HTTP URL for AI runtime API calls
 * 
 * @param {string} path - API path (e.g., '/api/workflows')
 * @param {string} [runtimeUrl] - Optional runtime base URL
 * @returns {string} Full HTTP URL
 */
export function buildRuntimeApiUrl(path, runtimeUrl = null) {
  if (runtimeUrl) {
    return `${runtimeUrl}${path}`;
  }
  
  // Same-origin fallback
  const protocol = window.location.protocol;
  const host = window.location.hostname;
  const port = import.meta.env.VITE_AI_RUNTIME_PORT || '8080';
  
  return `${protocol}//${host}:${port}${path}`;
}

/**
 * Check if AI features are available
 * This can be used to conditionally show/hide AI UI
 * 
 * @param {Object} userContext - The user context from auth
 * @returns {boolean} Whether AI features are enabled
 */
export function isAIEnabled(userContext) {
  // Check user's subscription/entitlements
  const aiEntitled = userContext?.entitlements?.includes('ai_access') ?? true;
  
  // Check environment flag
  const envEnabled = import.meta.env.VITE_AI_ENABLED !== 'false';
  
  return aiEntitled && envEnabled;
}

/**
 * Get auth headers for AI runtime API calls
 * Uses the same token as core for unified auth
 * 
 * @param {string} token - JWT access token
 * @returns {Object} Headers object
 */
export function getRuntimeAuthHeaders(token) {
  return {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json',
    'X-Auth-Source': 'mozaiks-core-ui',
  };
}

/**
 * Adapter to make ChatUI services work with core's auth
 */
export class CoreAuthAdapter {
  constructor(coreAuthContext) {
    this.coreAuth = coreAuthContext;
  }
  
  async getAccessToken() {
    if (this.coreAuth?.getAccessToken) {
      return await this.coreAuth.getAccessToken();
    }
    return null;
  }
  
  getCurrentUser() {
    return this.coreAuth?.user || null;
  }
  
  isAuthenticated() {
    return this.coreAuth?.isAuthenticated ?? false;
  }
  
  getUserId() {
    return this.coreAuth?.user?.user_id || this.coreAuth?.user?.sub || null;
  }
}

export default {
  buildRuntimeWebSocketUrl,
  buildRuntimeApiUrl,
  isAIEnabled,
  getRuntimeAuthHeaders,
  CoreAuthAdapter,
};
