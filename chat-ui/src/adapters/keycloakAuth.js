/**
 * KeycloakAuthAdapter — connects chat-ui's AuthAdapter interface to Keycloak.
 *
 * Reads /auth.json at init to get realm, clientId, and authority.
 * Uses keycloak-js for OIDC Authorization Code + PKCE flow.
 *
 * Usage:
 *   import { createKeycloakAuthAdapter } from './auth/KeycloakAuthAdapter';
 *
 *   const authAdapter = await createKeycloakAuthAdapter();
 *   // Pass to MozaiksApp: <MozaiksApp authAdapter={authAdapter} ... />
 */
import Keycloak from 'keycloak-js';
import { AuthAdapter } from './auth';

/**
 * Load auth.json from the brand public directory.
 * Served by Vite's publicDir at /auth.json.
 */
async function loadAuthConfig() {
  try {
    const resp = await fetch('/auth.json');
    if (!resp.ok) {
      console.warn('[keycloak-auth] auth.json not found, using defaults');
      return {};
    }
    return await resp.json();
  } catch (err) {
    console.warn('[keycloak-auth] Failed to load auth.json:', err);
    return {};
  }
}

/**
 * Keycloak auth adapter implementing the chat-ui AuthAdapter interface.
 */
export class KeycloakAuthAdapter extends AuthAdapter {
  /**
   * @param {Keycloak} keycloak — initialized keycloak-js instance
   * @param {object} authConfig — parsed auth.json
   */
  constructor(keycloak, authConfig = {}) {
    super();
    this.keycloak = keycloak;
    this.authConfig = authConfig;
    this._authStateCallbacks = [];
    this._currentUser = null;

    // Auto-refresh token before expiry
    this._refreshInterval = null;
    this._startTokenRefresh();
  }

  async getCurrentUser() {
    if (!this.keycloak.authenticated) {
      return null;
    }

    if (this._currentUser) {
      return this._currentUser;
    }

    // Build user object from Keycloak token claims
    const tokenParsed = this.keycloak.tokenParsed || {};
    const rolesConfig = this.authConfig.roles || {};
    const claimPath = rolesConfig.claimPath || 'realm_access.roles';

    // Extract roles using the configured claim path
    let roles = [];
    const parts = claimPath.split('.');
    let current = tokenParsed;
    for (const part of parts) {
      if (current && typeof current === 'object') {
        current = current[part];
      } else {
        current = undefined;
        break;
      }
    }
    if (Array.isArray(current)) {
      roles = current;
    }

    this._currentUser = {
      user_id: tokenParsed.sub || '',
      email: tokenParsed.email || '',
      name: tokenParsed.name || tokenParsed.preferred_username || '',
      roles,
      authenticated: true,
    };

    return this._currentUser;
  }

  async login() {
    await this.keycloak.login();
  }

  async logout() {
    const redirectUri = this.authConfig.keycloak?.logoutRedirectUri || '/';
    this._currentUser = null;
    this._notifyAuthStateChange(null);
    await this.keycloak.logout({ redirectUri: window.location.origin + redirectUri });
  }

  async refreshToken() {
    try {
      const refreshed = await this.keycloak.updateToken(30); // refresh if < 30s remaining
      if (refreshed) {
        // Token was refreshed — update user (claims may have changed)
        this._currentUser = null;
        const user = await this.getCurrentUser();
        this._notifyAuthStateChange(user);
      }
      return { success: true };
    } catch (err) {
      console.error('[keycloak-auth] Token refresh failed:', err);
      // Token refresh failed — session expired, redirect to login
      await this.login();
      return { success: false };
    }
  }

  getAccessToken() {
    return this.keycloak.token || null;
  }

  onAuthStateChange(callback) {
    this._authStateCallbacks.push(callback);
    // Immediately call with current state
    if (this.keycloak.authenticated) {
      this.getCurrentUser().then(user => callback(user));
    } else {
      callback(null);
    }
    // Return unsubscribe function
    return () => {
      this._authStateCallbacks = this._authStateCallbacks.filter(cb => cb !== callback);
    };
  }

  _notifyAuthStateChange(user) {
    this._authStateCallbacks.forEach(cb => cb(user));
  }

  _startTokenRefresh() {
    // Check token every 30 seconds and refresh if needed
    this._refreshInterval = setInterval(async () => {
      if (this.keycloak.authenticated) {
        try {
          await this.keycloak.updateToken(60); // refresh if < 60s remaining
        } catch {
          // Token expired and can't be refreshed
          this._currentUser = null;
          this._notifyAuthStateChange(null);
        }
      }
    }, 30000);
  }

  destroy() {
    if (this._refreshInterval) {
      clearInterval(this._refreshInterval);
    }
  }
}

/**
 * Create and initialize a Keycloak auth adapter.
 *
 * Reads /auth.json for configuration, initializes keycloak-js,
 * and returns a ready-to-use adapter.
 *
 * @param {object} [overrides] - Override auth.json values
 * @param {string} [overrides.authority] - Keycloak base URL
 * @param {string} [overrides.realm] - Keycloak realm name
 * @param {string} [overrides.clientId] - Keycloak public client ID
 * @returns {Promise<KeycloakAuthAdapter>}
 */
export async function createKeycloakAuthAdapter(overrides = {}) {
  const authConfig = await loadAuthConfig();
  const kcConfig = authConfig.keycloak || {};

  const authority = overrides.authority || kcConfig.authority || 'http://localhost:8080';
  const realm = overrides.realm || kcConfig.realm || 'mozaiks';
  const clientId = overrides.clientId || kcConfig.clientId || 'mozaiks-app';

  const keycloak = new Keycloak({
    url: authority,
    realm,
    clientId,
  });

  // Initialize with check-sso (silent login if session exists, no redirect if not)
  const features = authConfig.features || {};
  const initOptions = {
    onLoad: 'check-sso',
    pkceMethod: kcConfig.pkce !== false ? 'S256' : undefined,
    silentCheckSsoRedirectUri: window.location.origin + '/silent-check-sso.html',
    checkLoginIframe: false, // disable iframe check (doesn't work well with SPAs)
  };

  // Init keycloak-js — this MUST succeed when auth is enabled.
  // Throws on network failure, bad realm, or invalid clientId.
  await keycloak.init(initOptions);

  // Expose token globally for api.js fallback (window.mozaiksAuth)
  window.mozaiksAuth = {
    getAccessToken: () => keycloak.token || null,
  };

  const adapter = new KeycloakAuthAdapter(keycloak, authConfig);

  // Listen for Keycloak events
  keycloak.onAuthSuccess = async () => {
    const user = await adapter.getCurrentUser();
    adapter._notifyAuthStateChange(user);
  };

  keycloak.onAuthError = () => {
    adapter._currentUser = null;
    adapter._notifyAuthStateChange(null);
  };

  keycloak.onAuthRefreshError = () => {
    adapter._currentUser = null;
    adapter._notifyAuthStateChange(null);
  };

  keycloak.onTokenExpired = () => {
    keycloak.updateToken(30).catch(() => {
      adapter._currentUser = null;
      adapter._notifyAuthStateChange(null);
    });
  };

  return adapter;
}

export default KeycloakAuthAdapter;
