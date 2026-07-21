/**
 * platform — runtime environment bridge for chat-ui core.
 *
 * Abstracts browser-specific APIs (localStorage, window.location,
 * import.meta.env) behind a stable interface so that the core logic
 * (hooks, context, WebSocket) is portable to React Native.
 *
 * Web apps:        use the browser default — no configuration needed.
 * React Native:    call configurePlatform() once at app startup, before
 *                  mounting <ChatUIProvider />.
 *
 * @example React Native setup
 *
 *   // Use a synchronous key-value store (recommended: react-native-mmkv).
 *   // AsyncStorage does NOT work here because storage reads in useReducer
 *   // initialisers are synchronous.
 *   import { storage as mmkv } from './mmkvInstance';
 *   import { configurePlatform } from '@mozaiks/chat-ui/platform';
 *
 *   configurePlatform({
 *     storage: {
 *       getItem:    (key) => mmkv.getString(key) ?? null,
 *       setItem:    (key, value) => mmkv.set(key, value),
 *       removeItem: (key) => mmkv.delete(key),
 *     },
 *     getBaseUrls: () => ({
 *       httpUrl: 'https://api.myapp.com',
 *       wsUrl:   'wss://api.myapp.com',
 *     }),
 *   });
 *
 * @module @mozaiks/chat-ui/platform
 */

// ---------------------------------------------------------------------------
// Browser default — storage
// ---------------------------------------------------------------------------

const _browserStorage = {
  getItem(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  setItem(key, value) {
    try { localStorage.setItem(key, value); } catch { /* quota / security */ }
  },
  removeItem(key) {
    try { localStorage.removeItem(key); } catch { /* ignore */ }
  },
};

const _browserAuth = {
  getAccessToken() {
    try {
      if (typeof window !== 'undefined' && window.mozaiksAuth?.getAccessToken) {
        return window.mozaiksAuth.getAccessToken();
      }
      if (typeof window !== 'undefined' && window.mozaiksAuth?.token) {
        return window.mozaiksAuth.token;
      }
    } catch {
      // Fall through to storage-backed token lookup.
    }

    return _browserStorage.getItem('chatui_token') || _browserStorage.getItem('access_token');
  },
};

function _browserGetConfigOverrides() {
  if (typeof window !== 'undefined' && window.ChatUIConfig) {
    return window.ChatUIConfig;
  }

  return null;
}

function _stripTrailingSlash(url) {
  return typeof url === 'string' ? url.replace(/\/+$/, '') : url;
}

function _withPort(url, port) {
  if (!url || !port) {
    return _stripTrailingSlash(url);
  }

  try {
    const parsed = new URL(url);
    parsed.port = String(port);
    return _stripTrailingSlash(parsed.toString());
  } catch {
    return _stripTrailingSlash(url);
  }
}

// ---------------------------------------------------------------------------
// Browser default — base URLs
// ---------------------------------------------------------------------------

function _browserGetBaseUrls() {
  // Explicit Vite override takes priority
  const explicitHttp =
    typeof import.meta !== 'undefined' ? (import.meta.env?.VITE_API_URL ?? '') : '';
  if (explicitHttp) {
    const httpUrl = explicitHttp.replace(/\/+$/, '');
    const wsUrl   = httpUrl.replace(/^http/, 'ws');
    return { httpUrl, wsUrl };
  }

  if (typeof window !== 'undefined') {
    const proto   = window.location.protocol;
    const host    = window.location.hostname;
    const port    = window.location.port ? `:${window.location.port}` : '';
    const httpUrl = `${proto}//${host}${port}`;
    const wsUrl   = httpUrl.replace(/^http/, 'ws');
    return { httpUrl, wsUrl };
  }

  // Fallback for SSR / test environments
  return { httpUrl: 'http://localhost:8000', wsUrl: 'ws://localhost:8000' };
}

// ---------------------------------------------------------------------------
// Singleton — defaults to browser, overridable at runtime
// ---------------------------------------------------------------------------

let _storage    = _browserStorage;
let _auth       = _browserAuth;
let _getBaseUrls = _browserGetBaseUrls;
let _getConfigOverrides = _browserGetConfigOverrides;

/**
 * Configure the platform bridge for non-browser environments (React Native).
 *
 * Call this once before mounting <ChatUIProvider />.
 *
 * @param {Object}   impl
 * @param {Object}   [impl.storage]     - Key-value store. Must be synchronous.
 *                                        Implement: getItem(key), setItem(key, val), removeItem(key).
 * @param {Function} [impl.getBaseUrls] - Returns { httpUrl, wsUrl } for your backend.
 */
export function configurePlatform(impl = {}) {
  if (impl.storage)                        _storage     = impl.storage;
  if (impl.auth)                           _auth        = impl.auth;
  if (typeof impl.getBaseUrls === 'function') _getBaseUrls = impl.getBaseUrls;
  if (typeof impl.getConfigOverrides === 'function') _getConfigOverrides = impl.getConfigOverrides;
}

/**
 * The active platform bridge.
 *
 * Core files import this instead of calling window / localStorage directly.
 */
export const platform = {
  /** Synchronous key-value storage (localStorage on web, MMKV on RN). */
  get storage() { return _storage; },

  /** Access token resolver (window auth bridge on web, injected auth on RN). */
  getAccessToken() {
    return _auth?.getAccessToken?.() ?? null;
  },

  /**
   * Returns { httpUrl, wsUrl } for the Mozaiks runtime backend.
   * Override via configurePlatform() for React Native.
   */
  getBaseUrls() { return _getBaseUrls(); },

  /**
   * Resolve an HTTP base URL, optionally overriding the default port.
   */
  resolveHttpUrl({ explicitUrl = null, port = null } = {}) {
    if (explicitUrl) {
      return _stripTrailingSlash(explicitUrl);
    }

    const { httpUrl } = _getBaseUrls();
    return _withPort(httpUrl, port);
  },

  /**
   * Resolve a WebSocket base URL, optionally overriding the default port.
   */
  resolveWsUrl({ explicitUrl = null, port = null } = {}) {
    if (explicitUrl) {
      return _stripTrailingSlash(explicitUrl).replace(/^http/, 'ws');
    }

    const { wsUrl, httpUrl } = _getBaseUrls();
    return _withPort(wsUrl || httpUrl.replace(/^http/, 'ws'), port);
  },

  /**
   * Optional runtime config overrides supplied by the host environment.
   */
  getConfigOverrides() {
    return _getConfigOverrides?.() ?? null;
  },
};

export { createToolsLogger } from './workflowSurfaceRuntime.js';
export {
  useTransitionMotion,
  useTransitionChoiceMotion,
  TransitionChoicePanel,
  TransitionChoiceCard,
  TransitionActionPanel,
  TransitionActionButton,
} from './transitionPrimitives.jsx';

export default platform;
