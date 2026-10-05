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
  authorized_party: ({ claims }) => ({ id_token: jwt({ ...claims, aud: [claims.aud, 'other-client'] }) }),
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

const nativeCallback = 'org.mozaiks.examples.commonground:/auth/callback';
function nativeConfig() {
  const value = config();
  value.frontend.client_id = 'native-client';
  value.frontend.redirect_uri = nativeCallback;
  return value;
}

function nativeResponse({ url, callbackUri }) {
  const authorization = new URL(url);
  const now = Math.floor(Date.now() / 1000);
  const claims = {
    iss: issuer, aud: authorization.searchParams.get('client_id'), sub: 'native-user',
    nonce: authorization.searchParams.get('nonce'), exp: now + 3600, iat: now,
  };
  tokenResponse = { access_token: 'native-access-token', id_token: jwt(claims), token_type: 'Bearer', expires_in: 1800 };
  return { claims, callback: `${callbackUri}?code=native-code&state=${authorization.searchParams.get('state')}` };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('native transport reuses PKCE and the backend client, returns the trusted path, and avoids browser navigation', async () => {
  let opened;
  const adapter = createAuthAdapter({
    authConfig: nativeConfig(), appId: 'native-app',
    env: { VITE_OIDC_CLIENT_ID: 'unselected-client', VITE_OIDC_REDIRECT_URI: 'org.other.app:/auth/callback' },
    authorizationTransport: { async open(options) { opened = options; return nativeResponse(options).callback; } },
  });
  const users = [];
  const unsubscribe = adapter.onAuthStateChange(user => users.push(user));
  const result = await adapter.login({ returnPath: '/community/post-1?tab=comments' });
  unsubscribe();
  assert.equal(result.returnPath, '/community/post-1?tab=comments');
  assert.equal(users.at(-1).id, 'native-user');
  assert.equal(adapter.getAccessToken(), 'native-access-token');
  assert.equal(opened.callbackUri, nativeCallback);
  const authorization = new URL(opened.url);
  assert.equal(authorization.searchParams.get('client_id'), 'native-client');
  assert.equal(authorization.searchParams.get('redirect_uri'), nativeCallback);
  assert.equal(authorization.searchParams.get('code_challenge_method'), 'S256');
  const exchange = requests.find(request => request.method === 'POST');
  const body = new URLSearchParams(exchange.body);
  assert.equal(body.get('redirect_uri'), nativeCallback);
  assert.equal(body.get('client_id'), 'native-client');
  const challenge = Buffer.from(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(body.get('code_verifier')))).toString('base64url');
  assert.equal(challenge, authorization.searchParams.get('code_challenge'));
  assert.equal(window.location.assigned, undefined);
  assert.equal(window.location.pathname, '/apps');
});

test('native redirect configuration is explicit, scheme-bound, and matches the declared callback path', () => {
  for (const redirectUri of [
    '', 'myapp:/auth/callback', 'https://app.example/auth/callback',
    'org.example.app://host/auth/callback', 'org.example.app:///auth/callback',
    'org.example.app:/another/callback', 'org.example.app:/auth/callback?tenant=one',
    'org.example.app:/auth/callback#fragment', 'org.example.app:/auth/callback?',
    'org.example.app:/auth/../auth/callback', 'org.example.app:/auth/%63allback',
    'org.example.app-:/auth/callback',
  ]) {
    const authConfig = nativeConfig();
    authConfig.frontend.redirect_uri = redirectUri;
    assert.throws(() => createAuthAdapter({ authConfig, authorizationTransport: { open() {} } }), /Native OIDC redirect URI/);
  }
  assert.throws(() => createAuthAdapter({ authConfig: config(), authorizationTransport: {} }), /transport/);
  assert.throws(() => createAuthAdapter({ authConfig: nativeConfig() }), /HTTPS/);
  const authConfig = config();
  authConfig.frontend.redirect_uri = 'https://other.example/auth/callback';
  assert.throws(() => createAuthAdapter({ authConfig }), /application callback route/);
});

test('wrong callback URI cannot consume an active native transaction or exchange a token', async () => {
  const opened = deferred();
  const delivered = deferred();
  const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
    open(options) { opened.resolve(options); return delivered.promise; },
  } });
  const login = adapter.login();
  const options = await opened.promise;
  const callback = nativeResponse(options).callback;
  const before = [...storage.entries()];
  for (const wrong of [
    callback.replace('org.mozaiks', 'org.other'),
    callback.replace(':/auth/', '://host/auth/'),
    callback.replace('/auth/callback', '/different'),
    callback.replace('/auth/callback', '/auth/../auth/callback'),
    callback.replace('/auth/callback', '/auth/%63allback'),
    callback + '#fragment', callback + '#', `\n${callback}`,
  ]) {
    await assert.rejects(adapter.handleCallback(wrong), /callback URI/);
    assert.deepEqual([...storage.entries()], before);
  }
  assert.equal(requests.filter(request => request.method === 'POST').length, 0);
  delivered.resolve(callback);
  await login;
  assert.equal(adapter.getAccessToken(), 'native-access-token');
});

test('browser callback also validates its exact URI before consuming state', async () => {
  const { adapter, state } = await begin();
  await assert.rejects(adapter.handleCallback(`https://other.example/auth/callback?code=code-one&state=${state}`), /callback URI/);
  assert.equal(requests.filter(request => request.method === 'POST').length, 0);
  await adapter.handleCallback();
  assert.equal(adapter.getAccessToken(), 'opaque-access-token');
});

test('native cancellation removes the transaction and permits a fresh retry', async () => {
  let attempt = 0;
  let cancelledCallback;
  const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
    async open(options) {
      const { callback } = nativeResponse(options);
      if (attempt++ === 0) { cancelledCallback = callback; throw new Error('Authorization cancelled'); }
      return callback;
    },
  } });
  await assert.rejects(adapter.login(), /cancelled/);
  const key = [...storage.keys()].find(value => value.endsWith(':transactions'));
  assert.deepEqual(JSON.parse(storage.get(key)), {});
  assert.equal(adapter.getAccessToken(), null);
  await assert.rejects(adapter.handleCallback(cancelledCallback), /state/);
  assert.equal(requests.filter(request => request.method === 'POST').length, 0);
  await adapter.login();
  assert.equal(adapter.getAccessToken(), 'native-access-token');
});

test('a second native login cannot replace an outstanding browser operation', async () => {
  const opened = deferred();
  const delivered = deferred();
  let opens = 0;
  const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
    open(options) { opens += 1; opened.resolve(options); return delivered.promise; },
  } });
  const first = adapter.login();
  const options = await opened.promise;
  await assert.rejects(adapter.login(), /already in progress/);
  assert.equal(opens, 1);
  delivered.resolve(nativeResponse(options).callback);
  await first;
});

for (const variant of ['wrong_state', 'duplicate_state', 'duplicate_code', 'issuer', 'error']) {
  test(`native login rejects ${variant} before token exchange`, async () => {
    const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
      async open(options) {
        const callback = new URL(nativeResponse(options).callback);
        if (variant === 'wrong_state') callback.searchParams.set('state', 'unsolicited');
        if (variant === 'duplicate_state') callback.searchParams.append('state', callback.searchParams.get('state'));
        if (variant === 'duplicate_code') callback.searchParams.append('code', 'another-code');
        if (variant === 'issuer') callback.searchParams.set('iss', 'https://other.example');
        if (variant === 'error') callback.searchParams.set('error', 'access_denied');
        return callback.href;
      },
    } });
    await assert.rejects(adapter.login());
    assert.equal(requests.filter(request => request.method === 'POST').length, 0);
    assert.equal(adapter.getAccessToken(), null);
    const key = [...storage.keys()].find(value => value.endsWith(':transactions'));
    assert.deepEqual(JSON.parse(storage.get(key)), {});
  });
}

for (const [name, modify] of Object.entries(invalidResponses)) {
  test(`native code exchange preserves the ${name} identity check`, async () => {
    const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
      async open(options) {
        const response = nativeResponse(options);
        Object.assign(tokenResponse, modify(response));
        return response.callback;
      },
    } });
    await assert.rejects(adapter.login());
    assert.equal(await adapter.getCurrentUser(), null);
    assert.equal(adapter.getAccessToken(), null);
  });
}

test('native callback replay and a cold callback without a transaction cannot create identity', async () => {
  let callback;
  const options = { authConfig: nativeConfig(), authorizationTransport: {
    async open(request) { callback = nativeResponse(request).callback; return callback; },
  } };
  const adapter = createAuthAdapter(options);
  await adapter.login();
  requests.length = 0;
  await assert.rejects(adapter.handleCallback(callback), /state/);
  assert.equal(adapter.getAccessToken(), null);
  storage.clear();
  await assert.rejects(createAuthAdapter(options).handleCallback(callback), /state/);
  assert.equal(requests.length, 0);
});

test('duplicate native callback delivery shares one exchange while an unrelated callback is rejected', async () => {
  const opened = deferred();
  const delivered = deferred();
  const exchangeStarted = deferred();
  const exchangeDelivered = deferred();
  const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
    open(options) { opened.resolve(options); return delivered.promise; },
  } });
  const fetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (url === discovery.token_endpoint) {
      exchangeStarted.resolve();
      await exchangeDelivered.promise;
    }
    return fetch(url, options);
  };
  const login = adapter.login();
  const callback = nativeResponse(await opened.promise).callback;
  delivered.resolve(callback);
  await exchangeStarted.promise;
  const duplicate = adapter.handleCallback(callback);
  await assert.rejects(adapter.handleCallback(callback.replace('native-code', 'other-code')), /already in progress/);
  exchangeDelivered.resolve();
  assert.deepEqual(await duplicate, await login);
  assert.equal(requests.filter(request => request.method === 'POST').length, 1);
});

test('native logout clears local state, validates a new return state, and navigates to the local logout route', async () => {
  let logoutRequest;
  const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
    async open(options) {
      const url = new URL(options.url);
      if (url.pathname === '/authorize') return nativeResponse(options).callback;
      logoutRequest = { ...options, url };
      assert.equal(adapter.getAccessToken(), null);
      return `${options.callbackUri}?state=${url.searchParams.get('state')}`;
    },
  } });
  await adapter.login();
  const expectedIdToken = tokenResponse.id_token;
  await adapter.logout();
  assert.equal(logoutRequest.callbackUri, nativeCallback);
  assert.equal(logoutRequest.url.searchParams.get('post_logout_redirect_uri'), nativeCallback);
  assert.equal(logoutRequest.url.searchParams.get('id_token_hint'), expectedIdToken);
  assert.ok(logoutRequest.url.searchParams.get('state').length >= 43);
  assert.equal(window.location.assigned, '/login');
  assert.equal(adapter.getAccessToken(), null);
});

for (const variant of ['wrong_uri', 'wrong_state', 'duplicate_state', 'code', 'error', 'cancelled']) {
  test(`native logout rejects ${variant} while keeping local identity cleared`, async () => {
    const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
      async open(options) {
        const url = new URL(options.url);
        if (url.pathname === '/authorize') return nativeResponse(options).callback;
        if (variant === 'cancelled') throw new Error('Authorization cancelled');
        const callback = new URL(`${options.callbackUri}?state=${url.searchParams.get('state')}`);
        if (variant === 'wrong_uri') callback.pathname = '/different';
        if (variant === 'wrong_state') callback.searchParams.set('state', 'wrong');
        if (variant === 'duplicate_state') callback.searchParams.append('state', callback.searchParams.get('state'));
        if (variant === 'code') callback.searchParams.set('code', 'login-code');
        if (variant === 'error') callback.searchParams.set('error', 'access_denied');
        return callback.href;
      },
    } });
    await adapter.login();
    await assert.rejects(adapter.logout());
    assert.equal(adapter.getAccessToken(), null);
    assert.equal(window.location.assigned, undefined);
    await adapter.login();
    assert.equal(adapter.getAccessToken(), 'native-access-token');
  });
}

test('native logout rejects an expired return even with its matching state', async () => {
  const clock = Date.now;
  const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
    async open(options) {
      const url = new URL(options.url);
      if (url.pathname === '/authorize') return nativeResponse(options).callback;
      const expiredNow = clock() + 16 * 60 * 1000;
      Date.now = () => expiredNow;
      return `${options.callbackUri}?state=${url.searchParams.get('state')}`;
    },
  } });
  try {
    await adapter.login();
    await assert.rejects(adapter.logout(), /Invalid sign-out callback/);
    assert.equal(adapter.getAccessToken(), null);
    assert.equal(window.location.assigned, undefined);
  } finally {
    Date.now = clock;
  }
});

test('logout during native token exchange cannot restore a cancelled session', async () => {
  const exchangeStarted = deferred();
  const exchangeDelivered = deferred();
  const adapter = createAuthAdapter({ authConfig: nativeConfig(), authorizationTransport: {
    async open(options) { return nativeResponse(options).callback; },
  } });
  const fetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    if (url === discovery.token_endpoint) {
      exchangeStarted.resolve();
      await exchangeDelivered.promise;
    }
    return fetch(url, options);
  };
  const login = adapter.login();
  await exchangeStarted.promise;
  await assert.rejects(adapter.logout(), /already in progress/);
  exchangeDelivered.resolve();
  await assert.rejects(login, /cancelled/);
  assert.equal(adapter.getAccessToken(), null);
});
