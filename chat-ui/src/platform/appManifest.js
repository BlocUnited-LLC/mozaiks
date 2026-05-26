/**
 * App Manifest - Minimal Configuration
 *
 * For simple integration, you just need:
 * - appId: unique identifier for your app
 * - apiUrl: backend API URL
 * - wsUrl: WebSocket URL
 */

const DEFAULT_API_URL = 'http://localhost:8000';
const DEFAULT_WS_URL = 'ws://localhost:8000';

/**
 * Derive WebSocket URL from API URL if not provided
 */
export function deriveWsUrl(apiUrl = DEFAULT_API_URL) {
  if (/^wss?:\/\//i.test(apiUrl)) {
    return apiUrl;
  }
  if (/^https?:\/\//i.test(apiUrl)) {
    return apiUrl.replace(/^http/i, 'ws');
  }
  return DEFAULT_WS_URL;
}

/**
 * Create a simple app config
 *
 * @param {Object} options
 * @param {string} options.appId - Unique app identifier
 * @param {string} options.apiUrl - Backend API URL
 * @param {string} options.wsUrl - WebSocket URL (derived from apiUrl if not provided)
 * @param {string} options.userId - User ID for the session
 */
export function createAppConfig(options = {}) {
  const apiUrl = options.apiUrl || DEFAULT_API_URL;

  return {
    appId: options.appId || 'default',
    apiUrl,
    wsUrl: options.wsUrl || deriveWsUrl(apiUrl),
    userId: options.userId || 'anonymous',
  };
}

/**
 * Resolve app manifest from raw config.
 */
export function resolveAppManifest(rawManifest = {}, options = {}) {
  const appId = rawManifest.appId || options.appId || 'default';
  const apiUrl = options.apiUrl || rawManifest.apiUrl || DEFAULT_API_URL;
  const wsUrl = options.wsUrl || rawManifest.wsUrl || deriveWsUrl(apiUrl);

  return {
    appName: rawManifest.appName || 'Mozaiks App',
    appId,
    apiUrl,
    wsUrl,
  };
}
