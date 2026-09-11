import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { webcrypto } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const chatUiRoot = process.env.MOZAIKS_CHAT_UI_PATH || fileURLToPath(new URL('../../chat-ui', import.meta.url));
const { createAuthAdapter, loadShellAuth, safeReturnPath } = await import(
  pathToFileURL(resolve(chatUiRoot, 'src/auth/authAdapter.js')).href
);

const issuer = 'https://identity.example';
const discovery = {
  issuer,
  authorization_endpoint: `${issuer}/authorize`,
  token_endpoint: `${issuer}/token`,
  end_session_endpoint: `${issuer}/logout`,
};
function config() {
  return {
    required: true,
    contract: {
      schema_version: 'mozaiks.auth.v1', auth_required: true, strategy: 'oidc',
      routes: { login: '/login', callback: '/auth/callback', logout: '/login', post_login_default: '/apps' },
      frontend: {
        adapter: 'oidc_pkce', authority_env: 'VITE_OIDC_AUTHORITY', discovery_url_env: 'VITE_OIDC_DISCOVERY_URL',
        client_id_env: 'VITE_OIDC_CLIENT_ID', redirect_uri_env: 'VITE_OIDC_REDIRECT_URI', scope_env: 'VITE_OIDC_SCOPE',
        default_scopes: ['openid', 'profile', 'email'],
      },
    },
    runtime: { enabled: true, provider: 'jwt', local_development: false, user: null },
    frontend: { authority: issuer, discovery_url: '', client_id: 'browser-client', redirect_uri: '', scope: 'openid profile email' },
  };
}

let storage;
let requests;
let tokenResponse;
let metadataResponse;
function jwt(claims) {
  return `${Buffer.from('{"alg":"RS256"}').toString('base64url')}.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.signature`;
}
function move(path) { window.location.url = new URL(path, 'https://app.example'); }
beforeEach(() => {
  storage = new Map();
  requests = [];
  metadataResponse = discovery;
  globalThis.sessionStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: key => storage.delete(key),
  };
  globalThis.window = {
    crypto: webcrypto,
    location: {
      url: new URL('https://app.example/apps'),
      get origin() { return this.url.origin; },
      get href() { return this.url.href; },
      get pathname() { return this.url.pathname; },
      get search() { return this.url.search; },
      assign(value) { this.assigned = value; },
    },
    history: { replaceState(state, title, path) { move(path); } },
  };
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, ...options });
    return { ok: true, json: async () => url === discovery.token_endpoint ? tokenResponse : metadataResponse };
  };
});

async function begin(adapter = createAuthAdapter({ authConfig: config(), appId: 'app-one' })) {
  await adapter.login({ returnPath: '/apps/42?tab=build#latest' });
  const redirect = new URL(window.location.assigned);
  const nonce = redirect.searchParams.get('nonce');
  const state = redirect.searchParams.get('state');
  const now = Math.floor(Date.now() / 1000);
  const claims = { iss: issuer, aud: 'browser-client', sub: 'user-1', nonce, exp: now + 3600, iat: now, name: 'Zoë', roles: ['user'] };
  tokenResponse = { access_token: 'opaque-access-token', id_token: jwt(claims), token_type: 'Bearer', expires_in: 1800 };
  move(`/auth/callback?code=code-one&state=${state}`);
  return { adapter, redirect, claims, state };
}

test('PKCE login and a reload callback create one scoped session, notify listeners and restore the requested path', async () => {
  const { redirect } = await begin();
  assert.equal(redirect.searchParams.get('response_type'), 'code');
  assert.equal(redirect.searchParams.get('code_challenge_method'), 'S256');
  assert.equal(redirect.searchParams.get('redirect_uri'), 'https://app.example/auth/callback');
  assert.ok(redirect.searchParams.get('state').length >= 43);
  assert.ok(redirect.searchParams.get('nonce').length >= 43);
  const adapter = createAuthAdapter({ authConfig: config(), appId: 'app-one' });
  const users = [];
  const unsubscribe = adapter.onAuthStateChange(user => users.push(user));
  const [result, duplicate] = await Promise.all([adapter.handleCallback(), adapter.handleCallback()]);
  assert.deepEqual(result, duplicate);
  assert.equal(result.returnPath, '/apps/42?tab=build#latest');
  assert.equal((await adapter.getCurrentUser()).name, 'Zoë');
  assert.equal(users.at(-1).id, 'user-1');
  unsubscribe();
  assert.equal(adapter.getAccessToken(), 'opaque-access-token');
  const calls = requests.filter(request => request.method === 'POST');
  assert.equal(calls.length, 1);
  const verifier = new URLSearchParams(calls[0].body).get('code_verifier');
  const challenge = Buffer.from(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))).toString('base64url');
  assert.equal(challenge, redirect.searchParams.get('code_challenge'));
  assert.equal(window.location.search, '');
  const otherApp = createAuthAdapter({ authConfig: config(), appId: 'app-two' });
  assert.equal(await otherApp.getCurrentUser(), null);
});

for (const stateCase of ['missing', 'wrong', 'expired', 'future', 'replayed']) {
  test(`callback rejects ${stateCase} state before exchanging a token`, async () => {
    const { adapter, state } = await begin();
    if (stateCase === 'missing') {
      sessionStorage.setItem('app-one_pkce_verifier', 'retired-fallback');
      move('/auth/callback?code=code-one');
    }
    if (stateCase === 'wrong') move('/auth/callback?code=code-one&state=wrong');
    if (['expired', 'future'].includes(stateCase)) {
      const key = [...storage.keys()].find(key => key.endsWith(':transactions'));
      const pending = JSON.parse(storage.get(key));
      pending[state].createdAt = Date.now() + (stateCase === 'expired' ? -16 * 60000 : 60000);
      storage.set(key, JSON.stringify(pending));
    }
    if (stateCase === 'replayed') {
      await adapter.handleCallback();
      requests.length = 0;
      move(`/auth/callback?code=code-one&state=${state}`);
    }
    await assert.rejects(adapter.handleCallback(), /state/);
    assert.equal(requests.filter(request => request.method === 'POST').length, 0);
    assert.equal(await adapter.getCurrentUser(), null);
  });
}

const invalidResponses = {
  nonce: ({ claims }) => ({ id_token: jwt({ ...claims, nonce: 'wrong' }) }),
  issuer: ({ claims }) => ({ id_token: jwt({ ...claims, iss: 'https://other.example' }) }),
  audience: ({ claims }) => ({ id_token: jwt({ ...claims, aud: 'other-client' }) }),
  authorized_party: ({ claims }) => ({ id_token: jwt({ ...claims, aud: ['browser-client', 'other-client'] }) }),
  expired_identity: ({ claims }) => ({ id_token: jwt({ ...claims, exp: 1 }) }),
  future_identity: ({ claims }) => ({ id_token: jwt({ ...claims, iat: Date.now() / 1000 + 3600 }) }),
  missing_subject: ({ claims }) => ({ id_token: jwt({ ...claims, sub: '' }) }),
  missing_access_token: () => ({ access_token: undefined }),
  missing_id_token: () => ({ id_token: undefined }),
  unsigned_id_token: ({ claims }) => ({ id_token: `${Buffer.from('{"alg":"none"}').toString('base64url')}.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.` }),
  missing_lifetime: () => ({ expires_in: undefined }),
  wrong_token_type: () => ({ token_type: 'other' }),
};
for (const [name, modify] of Object.entries(invalidResponses)) {
  test(`rejects ${name} before storing authenticated state`, async () => {
    const login = await begin();
    Object.assign(tokenResponse, modify(login));
    await assert.rejects(login.adapter.handleCallback());
    assert.equal(await login.adapter.getCurrentUser(), null);
    assert.equal(login.adapter.getAccessToken(), null);
    assert.ok(![...storage.keys()].some(key => key.endsWith(':session')));
  });
}

test('opaque access token obeys expires_in and logout passes the ID token', async () => {
  const { adapter } = await begin();
  const expectedIdToken = tokenResponse.id_token;
  await adapter.handleCallback();
  const key = [...storage.keys()].find(key => key.endsWith(':session'));
  const session = JSON.parse(storage.get(key));
  assert.ok(session.expiresAt <= Date.now() + 1800000);
  await adapter.logout();
  assert.equal(new URL(window.location.assigned).searchParams.get('id_token_hint'), expectedIdToken);
  assert.equal(adapter.getAccessToken(), null);
  assert.equal(await adapter.getCurrentUser(), null);
  storage.set(key, JSON.stringify({ ...session, expiresAt: Date.now() - 1 }));
  assert.equal(adapter.getAccessToken(), null);
  assert.equal(storage.has(key), false);
});

test('discovery issuer and endpoint transport are validated before login redirect', async () => {
  const adapter = createAuthAdapter({ authConfig: config() });
  metadataResponse = { ...discovery, issuer: 'https://other.example' };
  await assert.rejects(adapter.login(), /issuer/);
  metadataResponse = { ...discovery, token_endpoint: 'http://identity.example/token' };
  await assert.rejects(adapter.login(), /HTTPS/);
  assert.equal(window.location.assigned, undefined);
});

test('return paths reject external, backslash and control-character redirects', () => {
  for (const path of ['https://other.example', '//other.example', '/\\other.example', '/%5cother.example', '/%0a/other.example', '/\n/other.example', '/\t/other.example']) {
    assert.equal(safeReturnPath(path, '/apps'), '/apps');
  }
  assert.equal(safeReturnPath('/apps?next=ok#details'), '/apps?next=ok#details');
});

test('declared build-time public OIDC settings work without backend copies and never enable mock mode', async () => {
  const authConfig = config();
  authConfig.frontend = null;
  const env = { VITE_OIDC_AUTHORITY: issuer, VITE_OIDC_CLIENT_ID: 'browser-client', VITE_OIDC_SCOPE: 'openid profile email custom', VITE_MOCK_MODE: 'true' };
  const adapter = createAuthAdapter({ authConfig, env });
  assert.equal(await adapter.getCurrentUser(), null);
  await adapter.login();
  assert.equal(new URL(window.location.assigned).searchParams.get('scope'), env.VITE_OIDC_SCOPE);
  authConfig.frontend = { client_id: 'backend-client', scope: 'openid profile email runtime' };
  await createAuthAdapter({ authConfig, env }).login();
  assert.equal(new URL(window.location.assigned).searchParams.get('client_id'), 'backend-client');
  assert.equal(new URL(window.location.assigned).searchParams.get('scope'), authConfig.frontend.scope);
  assert.throws(() => createAuthAdapter({ authConfig: { ...authConfig, frontend: null }, env: { VITE_MOCK_MODE: 'true' } }), /client ID/);
});

test('missing bootstrap, HTTP failure and conflicting modes cannot activate an app custom adapter or demo identity', async () => {
  let customCalls = 0;
  const custom = () => { customCalls += 1; return {}; };
  metadataResponse = {};
  await assert.rejects(loadShellAuth({ createAppAuthAdapter: custom }), /configuration/);
  globalThis.fetch = async () => ({ ok: false, status: 503 });
  await assert.rejects(loadShellAuth({ createAppAuthAdapter: custom }), /503/);
  const invalid = config();
  invalid.runtime.local_development = true;
  assert.throws(() => createAuthAdapter({ authConfig: invalid }), /Local development/);
  assert.equal(customCalls, 0);
  invalid.runtime.enabled = false;
  assert.throws(() => createAuthAdapter({ authConfig: invalid }), /Local development/);
});

test('only backend-authorized local identity is used and it sends no fabricated bearer token', async () => {
  const authConfig = config();
  authConfig.runtime = { enabled: false, provider: 'none', local_development: true, user: { id: 'local-operator', roles: ['user'] } };
  authConfig.frontend = null;
  metadataResponse = { appId: 'factory', auth: authConfig };
  const { authAdapter } = await loadShellAuth({ createAppAuthAdapter: () => { throw new Error('custom factory must not choose local identity'); } });
  assert.deepEqual(await authAdapter.getCurrentUser(), authConfig.runtime.user);
  assert.equal(authAdapter.getAccessToken(), null);
  authConfig.runtime.local_development = false;
  assert.throws(() => createAuthAdapter({ authConfig }), /required but unavailable/);
});

test('custom app adapter receives the verified backend projection without frontend mock fallback', async () => {
  const auth = config();
  metadataResponse = { appId: 'app-zero', auth };
  const expected = { getCurrentUser: async () => null, getAccessToken: () => null, onAuthStateChange: () => () => {}, login() {}, logout() {} };
  const { authAdapter, shellConfig } = await loadShellAuth({ createAppAuthAdapter: options => {
    assert.equal(options.authConfig, auth);
    assert.equal(options.appId, 'app-zero');
    return expected;
  } });
  assert.equal(authAdapter, expected);
  assert.equal(shellConfig.auth, auth);
});

for (const enabled of [true, false]) {
  test(`a declared public app remains anonymous with runtime auth enabled=${enabled}`, async () => {
    const auth = { required: false, contract: null, frontend: null, runtime: { enabled, provider: enabled ? 'jwt' : 'none', local_development: false, user: null } };
    metadataResponse = { appId: 'public-app', auth };
    const { authAdapter } = await loadShellAuth({ createAppAuthAdapter: () => { throw new Error('Public app must not invent custom identity'); } });
    assert.equal(await authAdapter.getCurrentUser(), null);
    assert.equal(authAdapter.getAccessToken(), null);
  });
}

test('missing, nonboolean, or contradictory public intent rejects bootstrap before app customization', async () => {
  for (const auth of [
    { ...config(), required: undefined },
    { ...config(), required: 'false' },
    { ...config(), required: false },
    { ...config(), contract: null },
    { ...config(), contract: { auth_required: false } },
  ]) {
    metadataResponse = { appId: 'malformed-app', auth };
    await assert.rejects(loadShellAuth({ createAppAuthAdapter: () => { throw new Error('custom adapter must not run'); } }), /authentication intent/);
  }
});
