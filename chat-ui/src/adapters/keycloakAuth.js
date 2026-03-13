/**
 * KeycloakAuthAdapter — connects chat-ui's AuthAdapter interface to Keycloak.
 *
 * Web-only adapter: depends on keycloak-js and browser globals.
 * React Native hosts should inject their own auth adapter via ChatUIProvider
 * or configurePlatform(), not import this module.
 *
 * Config is passed from the host app (via app.json) — no separate auth.json needed.
 * Uses keycloak-js for OIDC Authorization Code + PKCE flow.
 * Supports dev auto-login via direct access grants (Resource Owner Password).
 *
 * Usage:
 *
 * Web-only adapter: redirects via window.location and is intended for browser
 * development flows. Native hosts should provide their own auth adapter.
 *   import { createKeycloakAuthAdapter } from './auth/KeycloakAuthAdapter';
 *
 *   const authAdapter = await createKeycloakAuthAdapter({
 *     auth: { provider: 'keycloak' },
 *     dev: { autoLogin: true, users: [{ username: 'dev', password: 'dev' }] },
 *   });
 */
import Keycloak from 'keycloak-js';
import { AuthAdapter } from './auth';

/**
 * Keycloak auth adapter implementing the chat-ui AuthAdapter interface.
 */
export class KeycloakAuthAdapter extends AuthAdapter {
  /**
   * @param {Keycloak} keycloak — initialized keycloak-js instance
   * @param {object} appConfig — merged app config (auth + dev sections from app.json)
   */
  constructor(keycloak, appConfig = {}) {
    super();
    this.keycloak = keycloak;
    this.authConfig = appConfig.auth || {};
    this.appConfig = appConfig;
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
 * Reads config from the appConfig object (passed from app.json).
 * If dev.autoLogin is true and dev.users has entries, authenticates
 * automatically via Keycloak's direct access grant (Resource Owner Password)
 * so developers never see the login page during development.
 *
 * @param {object} [appConfig] - App config (auth + dev sections from app.json)
 * @param {object} [appConfig.auth] - Auth config: { provider, keycloak: { authority, realm, clientId } }
 * @param {object} [appConfig.dev] - Dev config: { autoLogin, users: [{ username, password }] }
 * @returns {Promise<KeycloakAuthAdapter>}
 */
export async function createKeycloakAuthAdapter(appConfig = {}) {
  const authSection = appConfig.auth || {};
  const kcConfig = authSection.keycloak || {};
  const devConfig = appConfig.dev || {};

  const authority = kcConfig.authority || 'http://localhost:8080';
  const realm = kcConfig.realm || 'mozaiks';
  const clientId = kcConfig.clientId || 'mozaiks-app';

  const keycloak = new Keycloak({
    url: authority,
    realm,
    clientId,
  });

  // ── Dev auto-login via direct access grant ─────────────────────────────
  // When dev.autoLogin is true and a dev user is declared, we fetch tokens
  // from the Keycloak token endpoint using the Resource Owner Password grant.
  // This requires directAccessGrantsEnabled=true on the Keycloak client.
  // The tokens are passed to keycloak.init() so no redirect is needed.
  if (devConfig.autoLogin && devConfig.users?.length) {
    const devUser = devConfig.users[0];
    const tokenUrl = `${authority}/realms/${realm}/protocol/openid-connect/token`;

    try {
      console.log(`[keycloak-auth] Dev auto-login as "${devUser.username}"...`);
      const resp = await fetch(tokenUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'password',
          client_id: clientId,
          username: devUser.username,
          password: devUser.password,
          scope: 'openid',
        }),
      });

      if (resp.ok) {
        const data = await resp.json();
        // Init keycloak-js with pre-fetched tokens — no redirect needed
        await keycloak.init({
          token: data.access_token,
          refreshToken: data.refresh_token,
          idToken: data.id_token,
          checkLoginIframe: false,
        });

        console.log(`[keycloak-auth] Dev auto-login successful (${devUser.username})`);

        window.mozaiksAuth = {
          getAccessToken: () => keycloak.token || null,
        };

        const adapter = new KeycloakAuthAdapter(keycloak, appConfig);
        _attachKeycloakListeners(keycloak, adapter);
        return adapter;
      }

      // Direct access grant failed — fall through to normal login
      const errBody = await resp.json().catch(() => ({}));
      console.warn(
        `[keycloak-auth] Dev auto-login failed (${errBody.error || resp.status}). ` +
        'Falling back to redirect login. ' +
        'Ensure directAccessGrantsEnabled=true on the Keycloak client.'
      );
    } catch (err) {
      console.warn('[keycloak-auth] Dev auto-login error:', err.message, '— falling back to redirect.');
    }
  }

  // ── Standard redirect login ────────────────────────────────────────────
  // 'login-required' redirects to Keycloak's login page if no session exists.
  // If the user already has a valid Keycloak session cookie, the redirect
  // resolves instantly without showing the login form.
  const initOptions = {
    onLoad: 'login-required',
    pkceMethod: kcConfig.pkce !== false ? 'S256' : undefined,
    silentCheckSsoRedirectUri: window.location.origin + '/_system/silent-check-sso.html',
    checkLoginIframe: false,
  };

  await keycloak.init(initOptions);

  // Expose token globally for api.js fallback (window.mozaiksAuth)
  window.mozaiksAuth = {
    getAccessToken: () => keycloak.token || null,
  };

  const adapter = new KeycloakAuthAdapter(keycloak, appConfig);
  _attachKeycloakListeners(keycloak, adapter);
  return adapter;
}

/**
 * Attach keycloak-js event listeners to the adapter.
 * @private
 */
function _attachKeycloakListeners(keycloak, adapter) {
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
}

export default KeycloakAuthAdapter;
