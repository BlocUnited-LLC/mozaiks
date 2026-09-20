/** Browser OIDC code flow. The host's validated auth contract owns configuration. */
const TRANSACTION_TTL_MS = 15 * 60 * 1000;

export function safeReturnPath(value, fallback = '/') {
  if (typeof value !== 'string') return fallback;
  let decoded;
  try { decoded = decodeURIComponent(value); } catch { return fallback; }
  if (!decoded.startsWith('/') || decoded.startsWith('//')
      || /[\\\u0000-\u0020\u007f]/.test(decoded)) return fallback;
  const target = new URL(value, window.location.origin);
  return target.origin === window.location.origin ? target.pathname + target.search + target.hash : fallback;
}

function publicUrl(value, label) {
  let url;
  try { url = new URL(value); } catch { throw new Error(`${label} must be an absolute URL`); }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback))
      || url.username || url.password || url.hash) throw new Error(`${label} must use HTTPS`);
  return url;
}

function randomValue() {
  const bytes = window.crypto.getRandomValues(new Uint8Array(32));
  return base64Url(bytes);
}

function base64Url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function jwtClaims(token) {
  if (typeof token !== 'string' || token.split('.').length !== 3) return null;
  try {
    const value = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const bytes = Uint8Array.from(atob(value), character => character.charCodeAt(0));
    const claims = JSON.parse(new TextDecoder().decode(bytes));
    return claims && typeof claims === 'object' && !Array.isArray(claims) ? claims : null;
  } catch { return null; }
}

function readJson(key) {
  try { return JSON.parse(sessionStorage.getItem(key)); } catch { return null; }
}

function fixedAdapter(user, routes) {
  return {
    routes,
    getCurrentUser: async () => user,
    getAccessToken: () => null,
    onAuthStateChange: callback => { callback(user); return () => {}; },
    login: async () => {},
    logout: async () => { window.location.assign(routes.logout); },
    handleCallback: async () => { throw new Error('No OIDC sign-in is active'); },
  };
}

export function validateAuthBootstrap(authConfig) {
  const runtime = authConfig?.runtime;
  if (!runtime || typeof runtime.enabled !== 'boolean'
      || typeof runtime.local_development !== 'boolean' || typeof runtime.provider !== 'string' || !runtime.provider.trim()) {
    throw new Error('The host did not provide a valid authentication configuration');
  }
  if (typeof authConfig.required !== 'boolean'
      || (authConfig.required && authConfig.contract?.auth_required !== true)
      || (!authConfig.required && authConfig.contract !== null)) {
    throw new Error('The host did not provide consistent application authentication intent');
  }
  if (runtime.local_development && (runtime.enabled || runtime.provider !== 'none')) {
    throw new Error('Local development identity cannot be used with enabled authentication');
  }
  return runtime;
}

export function createAuthAdapter({ authConfig, appId = 'mozaiks', env = {} } = {}) {
  const runtime = validateAuthBootstrap(authConfig);
  const contract = authConfig.contract;
  const routes = contract?.routes || { login: '/login', callback: '/auth/callback', logout: '/login', post_login_default: '/' };
  for (const key of ['login', 'callback', 'logout', 'post_login_default']) {
    const path = routes[key];
    if (safeReturnPath(path, null) !== path) throw new Error('Authentication routes must be safe local paths');
  }
  if (runtime.local_development) {
    const user = runtime.user;
    if (!user || typeof user.id !== 'string' || !user.id) {
      throw new Error('The host did not provide its local development identity');
    }
    return fixedAdapter(user, routes);
  }
  if (!authConfig.required) return fixedAdapter(null, routes);
  if (!runtime.enabled) {
    throw new Error('Authentication is required but unavailable');
  }
  if (contract?.schema_version !== 'mozaiks.auth.v1' || contract.strategy !== 'oidc'
      || contract.frontend?.adapter !== 'oidc_pkce') throw new Error('A canonical OIDC browser contract is required');

  // Build-time Vite values remain valid browser inputs. They never decide the
  // runtime's enabled/local mode; only the verified backend projection does.
  const frontend = Object.fromEntries(['authority', 'discovery_url', 'client_id', 'redirect_uri', 'scope'].map(key => {
    const publicValue = authConfig.frontend?.[key];
    const buildValue = env[contract.frontend[`${key}_env`]];
    return [key, (typeof publicValue === 'string' && publicValue.trim())
      || (typeof buildValue === 'string' && buildValue.trim()) || ''];
  }));
  if (!frontend.scope) frontend.scope = (contract.frontend.default_scopes || []).join(' ');
  const clientId = frontend?.client_id;
  if (typeof clientId !== 'string' || !clientId.trim()) throw new Error('OIDC client ID is missing');
  const authority = frontend.authority ? publicUrl(frontend.authority, 'OIDC authority').href.replace(/\/$/, '') : '';
  const discoveryUrl = publicUrl(frontend.discovery_url || (authority && `${authority}/.well-known/openid-configuration`), 'OIDC discovery URL').href;
  const redirectUri = publicUrl(frontend.redirect_uri || new URL(routes.callback, window.location.origin).href, 'OIDC redirect URI').href;
  if (redirectUri !== new URL(routes.callback, window.location.origin).href) {
    throw new Error('OIDC redirect URI must match this application callback route');
  }
  const scope = frontend.scope;
  if (typeof scope !== 'string' || !scope.split(/\s+/).includes('openid')) throw new Error('OIDC scope must include openid');

  const prefix = `mozaiks:${encodeURIComponent(appId)}:${encodeURIComponent(clientId)}:${encodeURIComponent(discoveryUrl)}`;
  const sessionKey = `${prefix}:session`;
  const transactionKey = `${prefix}:transactions`;
  const listeners = new Set();
  let metadataPromise;
  let callbackPromise;
  let expirationTimer;

  function readSession() {
    const session = readJson(sessionKey);
    if (!session || typeof session.accessToken !== 'string' || !session.accessToken
        || !Number.isFinite(session.expiresAt) || session.expiresAt <= Date.now()
        || typeof session.user?.id !== 'string' || !session.user.id) {
      sessionStorage.removeItem(sessionKey);
      return null;
    }
    return session;
  }

  function notify() {
    clearTimeout(expirationTimer);
    const session = readSession();
    for (const callback of listeners) callback(session?.user || null);
    if (session && listeners.size) {
      expirationTimer = setTimeout(notify, Math.min(session.expiresAt - Date.now() + 1, 2147483647));
    }
  }

  function transactions() {
    const stored = readJson(transactionKey);
    return Object.fromEntries(Object.entries(stored && typeof stored === 'object' ? stored : {}).filter(([, entry]) => (
      entry && typeof entry.verifier === 'string' && typeof entry.nonce === 'string'
      && Number.isFinite(entry.createdAt) && entry.createdAt <= Date.now()
      && Date.now() - entry.createdAt < TRANSACTION_TTL_MS && entry.redirectUri === redirectUri
    )));
  }

  async function metadata() {
    if (!metadataPromise) {
      metadataPromise = (async () => {
        const response = await fetch(discoveryUrl, { headers: { Accept: 'application/json' } });
        if (!response.ok) throw new Error(`OIDC discovery failed: HTTP ${response.status}`);
        const document = await response.json();
        const issuer = publicUrl(document.issuer, 'OIDC issuer').href.replace(/\/$/, '');
        if (authority && issuer !== authority) throw new Error('OIDC discovery issuer does not match the configured authority');
        publicUrl(document.authorization_endpoint, 'OIDC authorization endpoint');
        publicUrl(document.token_endpoint, 'OIDC token endpoint');
        if (document.end_session_endpoint) publicUrl(document.end_session_endpoint, 'OIDC logout endpoint');
        return document;
      })().catch(error => { metadataPromise = undefined; throw error; });
    }
    return metadataPromise;
  }

  async function exchangeCallback() {
    const params = new URLSearchParams(window.location.search);
    const state = params.get('state');
    const pending = transactions();
    const transaction = state && Object.hasOwn(pending, state) ? pending[state] : null;
    if (!transaction) throw new Error('Login state is missing or expired. Restart sign-in.');
    delete pending[state];
    sessionStorage.setItem(transactionKey, JSON.stringify(pending));
    const cleanUrl = new URL(window.location.href);
    for (const key of ['code', 'state', 'error', 'error_description', 'session_state', 'iss']) cleanUrl.searchParams.delete(key);
    window.history.replaceState(window.history.state, '', cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
    if (params.has('error')) throw new Error('Sign-in was not completed. Please try again.');
    const code = params.get('code');
    if (!code || params.getAll('code').length !== 1 || params.getAll('state').length !== 1) throw new Error('Invalid sign-in callback');
    const document = await metadata();
    if (params.has('iss') && params.get('iss') !== document.issuer) throw new Error('Sign-in callback issuer mismatch');
    const response = await fetch(document.token_endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'authorization_code', client_id: clientId, redirect_uri: redirectUri, code, code_verifier: transaction.verifier }).toString(),
    });
    if (!response.ok) throw new Error(`Token exchange failed: HTTP ${response.status}`);
    const data = await response.json();
    const claims = jwtClaims(data.id_token);
    let header;
    try { header = JSON.parse(atob(data.id_token.split('.')[0].replace(/-/g, '+').replace(/_/g, '/'))); } catch { header = null; }
    const now = Date.now() / 1000;
    const audiences = typeof claims?.aud === 'string' ? [claims.aud] : claims?.aud;
    // Tokens come directly from the configured HTTPS token endpoint. Validate
    // OIDC identity claims before using them; the API independently verifies access tokens.
    if (!claims || typeof header?.alg !== 'string' || header.alg === 'none' || !data.id_token.split('.')[2]
        || claims.iss !== document.issuer || !Array.isArray(audiences) || !audiences.includes(clientId)
        || (audiences.length > 1 && claims.azp !== clientId) || (claims.azp && claims.azp !== clientId)
        || typeof claims.sub !== 'string' || !claims.sub || claims.nonce !== transaction.nonce
        || !Number.isFinite(claims.exp) || claims.exp <= now
        || !Number.isFinite(claims.iat) || claims.iat > now + 60) throw new Error('Invalid OIDC identity token');
    if (typeof data.access_token !== 'string' || !data.access_token.trim()
        || typeof data.token_type !== 'string' || data.token_type.toLowerCase() !== 'bearer') throw new Error('Invalid OIDC access token response');
    const accessClaims = jwtClaims(data.access_token);
    const lifetime = Number.isFinite(data.expires_in) && data.expires_in > 0 ? now + data.expires_in : accessClaims?.exp;
    const expiresAt = Math.min(lifetime, claims.exp, Number.isFinite(accessClaims?.exp) ? accessClaims.exp : Infinity) * 1000;
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) throw new Error('OIDC token lifetime is missing or expired');
    const roles = claims.realm_access?.roles || claims.roles;
    const user = {
      id: claims.sub, user_id: claims.sub,
      email: typeof claims.email === 'string' ? claims.email : null,
      name: claims.name || claims.preferred_username || claims.email || claims.sub,
      firstName: claims.given_name || null, lastName: claims.family_name || null,
      roles: Array.isArray(roles) ? roles.filter(role => typeof role === 'string') : [],
    };
    sessionStorage.setItem(sessionKey, JSON.stringify({ accessToken: data.access_token, idToken: data.id_token, expiresAt, user }));
    notify();
    return { returnPath: safeReturnPath(transaction.returnPath, routes.post_login_default) };
  }

  return {
    routes,
    getCurrentUser: async () => readSession()?.user || null,
    getAccessToken: () => readSession()?.accessToken || null,
    onAuthStateChange: callback => {
      listeners.add(callback);
      notify();
      return () => { listeners.delete(callback); if (!listeners.size) clearTimeout(expirationTimer); };
    },
    login: async ({ returnPath } = {}) => {
      const document = await metadata();
      const verifier = randomValue();
      const challenge = base64Url(new Uint8Array(await window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
      const state = randomValue();
      const nonce = randomValue();
      const pending = transactions();
      pending[state] = { verifier, nonce, createdAt: Date.now(), redirectUri, returnPath: safeReturnPath(returnPath, routes.post_login_default) };
      sessionStorage.setItem(transactionKey, JSON.stringify(pending));
      const url = new URL(document.authorization_endpoint);
      for (const [key, value] of Object.entries({ client_id: clientId, redirect_uri: redirectUri, response_type: 'code', scope, code_challenge: challenge, code_challenge_method: 'S256', state, nonce })) url.searchParams.set(key, value);
      window.location.assign(url.href);
    },
    logout: async () => {
      const session = readSession();
      sessionStorage.removeItem(sessionKey);
      sessionStorage.removeItem(transactionKey);
      notify();
      let document;
      try { document = await metadata(); } catch { /* Local logout still succeeds. */ }
      if (!document?.end_session_endpoint) { window.location.assign(routes.logout); return; }
      const url = new URL(document.end_session_endpoint);
      url.searchParams.set('client_id', clientId);
      url.searchParams.set('post_logout_redirect_uri', new URL(routes.logout, window.location.origin).href);
      if (session?.idToken) url.searchParams.set('id_token_hint', session.idToken);
      window.location.assign(url.href);
    },
    handleCallback: () => {
      if (!callbackPromise) {
        callbackPromise = exchangeCallback().catch(error => {
          sessionStorage.removeItem(sessionKey);
          notify();
          throw error;
        }).finally(() => { callbackPromise = undefined; });
      }
      return callbackPromise;
    },
  };
}

/** One verified bootstrap, shared by shell navigation and the chosen auth adapter. */
export async function loadShellAuth({ apiBaseUrl = '', createAppAuthAdapter, env = {} } = {}) {
  const response = await fetch(`${apiBaseUrl.replace(/\/$/, '')}/api/shell-config`, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`Application sign-in configuration failed: HTTP ${response.status}`);
  const shellConfig = await response.json();
  const runtime = validateAuthBootstrap(shellConfig.auth);
  const options = { authConfig: shellConfig.auth, appId: shellConfig.appId, env };
  const authAdapter = await (runtime.enabled && shellConfig.auth.required && typeof createAppAuthAdapter === 'function'
    ? createAppAuthAdapter(options) : createAuthAdapter(options));
  for (const name of ['getCurrentUser', 'getAccessToken', 'onAuthStateChange', 'login', 'logout']) {
    if (typeof authAdapter?.[name] !== 'function') throw new Error(`App authentication adapter is missing ${name}`);
  }
  return { authAdapter, shellConfig };
}
