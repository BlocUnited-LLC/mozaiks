/**
 * Platform bridge setup.
 *
 * MUST be imported before any shared-core component is mounted.
 * index.js imports this as the very first side-effect so it runs before
 * App.tsx is evaluated.
 *
 * Provides the shared chat-ui core with:
 *   - Synchronous MMKV storage (required — AsyncStorage cannot be used)
 *   - Backend base URLs (reads from env or falls back to local dev default)
 *   - Auth token resolver (reads from MMKV; set by your auth flow on login)
 */

import { configurePlatform } from '@mozaiks/chat-ui/platform';
import { storage } from './mmkvInstance';
import appConfig, { getMobilePlatformConfig } from './appConfig';
import { createMobilePlatformAuthBridge } from '../auth/createAuthAdapter';

// ---------------------------------------------------------------------------
// Backend URLs
// ---------------------------------------------------------------------------
// Override by setting MOZAIKS_API_URL / MOZAIKS_WS_URL in your .env (via
// react-native-config or another env loader) before shipping to production.
// ---------------------------------------------------------------------------

const mobileConfig = getMobilePlatformConfig();

if (!mobileConfig.enabled) {
  throw new Error('Mobile platform is disabled in platform/app.json (platforms.mobile.enabled=false).');
}

const API_URL = (typeof process !== 'undefined' && process.env.MOZAIKS_API_URL)
  || appConfig.apiUrl
  || 'http://localhost:8000';

const WS_URL = (typeof process !== 'undefined' && process.env.MOZAIKS_WS_URL)
  || appConfig.wsUrl
  || API_URL.replace(/^http/, 'ws');

// ---------------------------------------------------------------------------
// Configure
// ---------------------------------------------------------------------------

configurePlatform({
  storage: {
    getItem:    (key: string) => storage.getString(key) ?? null,
    setItem:    (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  },

  auth: createMobilePlatformAuthBridge(),

  getBaseUrls: () => ({
    httpUrl: API_URL,
    wsUrl:   WS_URL,
  }),
} as any);
