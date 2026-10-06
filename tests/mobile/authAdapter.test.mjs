import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import { createHash, webcrypto } from 'node:crypto';
import { createAuthAdapter } from '../../chat-ui/src/auth/authAdapter.js';
import { createNativeAppAuthAdapter } from '../../factory_app/build_context/mobile/templates/ui/auth/capacitor/authAdapter.mjs';

const callbackUri = 'org.mozaiks.examples.commonground:/auth/callback';
const issuer = 'https://identity.example';
const receiptKey = 'mozaiks:common-ground:native-callback-delivery';
const digest = value => createHash('sha256').update(value).digest('hex');
const jwt = claims => `${Buffer.from('{"alg":"RS256"}').toString('base64url')}.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.fixture-signature`;

function options() {
  return {
    appId: 'common-ground',
    env: { VITE_OIDC_REDIRECT_URI: callbackUri },
    authConfig: {
      required: true,
      runtime: { enabled: true, provider: 'jwt', local_development: false, user: null },
      contract: {
        schema_version: 'mozaiks.auth.v1', auth_required: true, strategy: 'oidc',
        routes: { login: '/login', callback: '/auth/callback', logout: '/login', post_login_default: '/community' },
        frontend: { adapter: 'oidc_pkce', default_scopes: ['openid', 'profile', 'email'] },
      },
      frontend: {
        authority: issuer, client_id: 'browser-client', redirect_uri: 'https://app.example/auth/callback',
        scope: 'openid profile email', android: { client_id: 'native-client', redirect_uri: callbackUri },
      },
    },
  };
}

let storage;
let requests;
let tokenResponse;
let nativeWindow;
let navigation;
beforeEach(() => {
  storage = new Map();
  requests = [];
  navigation = [];
  const sessionStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: key => storage.delete(key),
  };
  nativeWindow = {
    crypto: webcrypto,
    sessionStorage,
    location: { origin: 'https://localhost', href: 'https://localhost/login', assign: path => navigation.push(path) },
    history: { state: null, replaceState: (_state, _title, path) => navigation.push(path) },
    PopStateEvent: class { constructor(type) { this.type = type; } },
    dispatchEvent: event => navigation.push(event.type),
  };
  globalThis.window = nativeWindow;
  globalThis.sessionStorage = sessionStorage;
  globalThis.fetch = async (url, request = {}) => {
    requests.push({ url, ...request });
    return { ok: true, json: async () => url === `${issuer}/token` ? tokenResponse : {
      issuer, authorization_endpoint: `${issuer}/authorize`, token_endpoint: `${issuer}/token`,
    } };
  };
});

function grant(url) {
  const request = new URL(url);
  assert.equal(request.searchParams.get('client_id'), 'native-client');
  assert.equal(request.searchParams.get('redirect_uri'), callbackUri);
  const now = Math.floor(Date.now() / 1000);
  tokenResponse = {
    access_token: 'fixture-native-access-token', token_type: 'Bearer', expires_in: 1800,
    id_token: jwt({ iss: issuer, aud: 'native-client', sub: 'native-member',
      nonce: request.searchParams.get('nonce'), exp: now + 1800, iat: now }),
  };
  return `${callbackUri}?code=fixture-authorization-code&state=${request.searchParams.get('state')}`;
}

function nativeHost(launchUrl) {
  const appListeners = new Map();
  const browserListeners = new Map();
  const host = { launchUrl, callback: null, handled: [], opens: 0, removed: 0, cancel: false };
  const listen = (listeners, name, listener) => {
    listeners.set(name, listener);
    return Promise.resolve({ remove: async () => { listeners.delete(name); host.removed += 1; } });
  };
  host.dependencies = {
    platform: 'android', window: nativeWindow,
    App: {
      getLaunchUrl: async () => host.launchUrl ? { url: host.launchUrl } : undefined,
      addListener: (name, listener) => listen(appListeners, name, listener),
    },
    Browser: {
      addListener: (name, listener) => listen(browserListeners, name, listener),
      async open({ url }) {
        host.opens += 1;
        if (host.cancel) { browserListeners.get('browserFinished')(); return; }
        host.callback = grant(url);
        appListeners.get('appUrlOpen')({ url: host.callback });
      },
    },
    createSharedAuthAdapter(input) {
      const adapter = createAuthAdapter(input);
      return { ...adapter, handleCallback(url) { host.handled.push(url); return adapter.handleCallback(url); } };
    },
  };
  return host;
}

test('warm native login stores only a SHA256 delivery receipt and cleans its listeners', async () => {
  const host = nativeHost();
  const adapter = await createNativeAppAuthAdapter(options(), host.dependencies);
  const result = await adapter.login({ returnPath: '/community/post-1' });
  assert.equal(result.returnPath, '/community/post-1');
  assert.equal(adapter.getAccessToken(), 'fixture-native-access-token');
  assert.equal(storage.get(receiptKey), digest(host.callback));
  assert.match(storage.get(receiptKey), /^[a-f0-9]{64}$/);
  assert.ok(!storage.get(receiptKey).includes('fixture-authorization-code'));
  assert.ok(!storage.get(receiptKey).includes('fixture-native-access-token'));
  assert.equal(host.removed, 2);
  assert.equal(requests.filter(request => request.method === 'POST').length, 1);
});

test('WebView reload ignores the retained callback receipt and preserves the authenticated session', async () => {
  const host = nativeHost();
  const first = await createNativeAppAuthAdapter(options(), host.dependencies);
  await first.login();
  host.launchUrl = host.callback;
  const reloaded = await createNativeAppAuthAdapter(options(), host.dependencies);
  assert.equal(reloaded.getAccessToken(), 'fixture-native-access-token');
  assert.equal((await reloaded.getCurrentUser()).id, 'native-member');
  assert.deepEqual(host.handled, []);
  assert.deepEqual(navigation, []);
  assert.equal(requests.filter(request => request.method === 'POST').length, 1);
});

test('a cold launch with a surviving PKCE transaction completes through the shared adapter', async () => {
  let deliverRequest;
  let abandonPreviousPage;
  const opened = new Promise(resolve => { deliverRequest = resolve; });
  const previousOptions = options();
  previousOptions.authConfig.frontend = { ...previousOptions.authConfig.frontend, ...previousOptions.authConfig.frontend.android };
  const previous = createAuthAdapter({ ...previousOptions, authorizationTransport: {
    open(request) { deliverRequest(request); return new Promise((_resolve, reject) => { abandonPreviousPage = reject; }); },
  } });
  const unfinished = previous.login({ returnPath: '/community/post-2?tab=comments' });
  const stopped = assert.rejects(unfinished, /Previous WebView ended/);
  const callback = grant((await opened).url);
  const host = nativeHost(callback);
  try {
    const adapter = await createNativeAppAuthAdapter(options(), host.dependencies);
    assert.equal(adapter.getAccessToken(), 'fixture-native-access-token');
    assert.deepEqual(host.handled, [callback]);
    assert.deepEqual(navigation, ['/community/post-2?tab=comments', 'popstate']);
    assert.equal(storage.get(receiptKey), digest(callback));
    assert.equal(requests.filter(request => request.method === 'POST').length, 1);
    assert.equal(host.opens, 0);
  } finally {
    abandonPreviousPage(new Error('Previous WebView ended'));
    await stopped;
  }
});

test('a cold callback without a surviving transaction is ignored without native bridge work', async () => {
  const callback = `${callbackUri}?code=fixture-authorization-code&state=lost-transaction`;
  const host = nativeHost(callback);
  host.dependencies.App.getLaunchUrl = () => assert.fail('No transaction can consume this launch intent');
  const adapter = await createNativeAppAuthAdapter(options(), host.dependencies);
  assert.equal(adapter.getAccessToken(), null);
  assert.equal(await adapter.getCurrentUser(), null);
  assert.deepEqual(navigation, []);
  assert.equal(requests.length, 0);
  assert.equal(storage.has(receiptKey), false);
  assert.deepEqual([...storage.entries()], []);
  navigation.length = 0;
  await createNativeAppAuthAdapter(options(), host.dependencies);
  assert.equal(host.handled.length, 0);
  assert.deepEqual(navigation, []);
});

for (const failure of ['rejection', 'timeout']) {
  test(`pending native authorization survives launch-intent ${failure} and permits fresh sign-in`, async t => {
    let deliverRequest;
    let abandonPreviousPage;
    const opened = new Promise(resolve => { deliverRequest = resolve; });
    const previousOptions = options();
    previousOptions.authConfig.frontend = { ...previousOptions.authConfig.frontend, ...previousOptions.authConfig.frontend.android };
    const previous = createAuthAdapter({ ...previousOptions, authorizationTransport: {
      open(request) { deliverRequest(request); return new Promise((_resolve, reject) => { abandonPreviousPage = reject; }); },
    } });
    const unfinished = previous.login({ returnPath: '/community/retained' });
    const stopped = assert.rejects(unfinished, /Previous WebView ended/);
    const callback = grant((await opened).url);
    const transactionKey = [...storage.keys()].find(key => key.endsWith(':transactions'));
    const transactionBefore = storage.get(transactionKey);
    const host = nativeHost();
    let launchRequested;
    let deliverLateLaunch;
    const requested = new Promise(resolve => { launchRequested = resolve; });
    host.dependencies.App.getLaunchUrl = () => {
      launchRequested();
      return failure === 'rejection'
        ? Promise.reject(new Error('Native bridge unavailable'))
        : new Promise(resolve => { deliverLateLaunch = resolve; });
    };
    t.mock.timers.enable({ apis: ['setTimeout'] });
    try {
      const boot = createNativeAppAuthAdapter(options(), host.dependencies);
      await requested;
      if (failure === 'timeout') t.mock.timers.tick(5000);
      const adapter = await boot;
      t.mock.timers.reset();
      assert.equal(adapter.hasPendingAuthorization(), true);
      assert.equal(storage.get(transactionKey), transactionBefore);
      assert.equal(adapter.getAccessToken(), null);
      assert.equal(storage.has(receiptKey), false);
      assert.deepEqual(navigation, []);
      assert.equal(requests.filter(request => request.method === 'POST').length, 0);
      if (failure === 'timeout') {
        deliverLateLaunch({ url: callback });
        await Promise.resolve();
        await Promise.resolve();
        assert.equal(adapter.getAccessToken(), null);
        assert.equal(storage.has(receiptKey), false);
        assert.deepEqual(host.handled, []);
      }
      const result = await adapter.login({ returnPath: '/community/fresh' });
      assert.equal(result.returnPath, '/community/fresh');
      assert.equal(adapter.getAccessToken(), 'fixture-native-access-token');
      assert.equal(host.opens, 1);
      assert.equal(requests.filter(request => request.method === 'POST').length, 1);
    } finally {
      t.mock.timers.reset();
      abandonPreviousPage(new Error('Previous WebView ended'));
      await stopped;
    }
  });
}

test('a post-logout bootstrap cannot wait on an irrelevant retained native launch intent', async () => {
  const host = nativeHost();
  const first = await createNativeAppAuthAdapter(options(), host.dependencies);
  await first.login();
  await first.logout(); // The fixture issuer has no external logout endpoint.
  assert.equal(first.hasPendingAuthorization(), false);
  host.dependencies.App.getLaunchUrl = () => new Promise(() => {});
  const reloaded = await Promise.race([
    createNativeAppAuthAdapter(options(), host.dependencies),
    new Promise((_, reject) => setTimeout(() => reject(new Error('Bootstrap waited on retained intent')), 100)),
  ]);
  assert.equal(reloaded.getAccessToken(), null);
});

test('cancelled native authorization creates no receipt and a fresh attempt can succeed', async () => {
  const host = nativeHost();
  host.cancel = true;
  const adapter = await createNativeAppAuthAdapter(options(), host.dependencies);
  await assert.rejects(adapter.login(), /cancelled/);
  assert.equal(storage.has(receiptKey), false);
  assert.equal(adapter.getAccessToken(), null);
  assert.equal(host.removed, 2);
  host.cancel = false;
  await adapter.login();
  assert.equal(adapter.getAccessToken(), 'fixture-native-access-token');
  assert.equal(host.removed, 4);
  assert.equal(storage.get(receiptKey), digest(host.callback));
});

test('non-Android hosts fail before native plugin or shared adapter initialization', async () => {
  for (const platform of ['web', 'ios', undefined]) {
    await assert.rejects(createNativeAppAuthAdapter(options(), {
      platform, window: nativeWindow,
      createSharedAuthAdapter: () => assert.fail('Unsupported host initialized auth'),
      App: { getLaunchUrl: () => assert.fail('Unsupported host called App') },
      Browser: {},
    }), /requires Android/);
  }
  assert.equal(storage.size, 0);
});

test('browser and Android select independent clients from the same backend projection', async () => {
  const input = options();
  nativeWindow.location.origin = 'https://app.example';
  nativeWindow.location.href = 'https://app.example/login';
  const browser = createAuthAdapter(input);
  await browser.login();
  const browserRequest = new URL(navigation.at(-1));
  assert.equal(browserRequest.searchParams.get('client_id'), 'browser-client');
  assert.equal(browserRequest.searchParams.get('redirect_uri'), 'https://app.example/auth/callback');
  const native = await createNativeAppAuthAdapter(input, nativeHost().dependencies);
  await native.login();
  assert.equal(input.authConfig.frontend.client_id, 'browser-client');
  assert.equal(input.authConfig.frontend.redirect_uri, 'https://app.example/auth/callback');
  assert.equal(native.getAccessToken(), 'fixture-native-access-token');
  assert.equal(browser.getAccessToken(), null);
});

test('missing, shared, or mismatched Android registrations fail before native plugin use', async () => {
  const mutations = [
    input => { delete input.authConfig.frontend.android; },
    input => { input.authConfig.frontend.android.client_id = ''; },
    input => { input.authConfig.frontend.android.client_id = 'browser-client'; },
    input => { input.authConfig.frontend.android.redirect_uri = 'org.other.app:/auth/callback'; },
    input => { delete input.env.VITE_OIDC_REDIRECT_URI; },
  ];
  for (const mutate of mutations) {
    const input = options();
    mutate(input);
    await assert.rejects(createNativeAppAuthAdapter(input, {
      platform: 'android', window: nativeWindow,
      createSharedAuthAdapter: () => assert.fail('Invalid profile reached shared auth'),
      App: { getLaunchUrl: () => assert.fail('Invalid profile called native plugin') },
      Browser: {},
    }), /registered Android|Android callback/);
  }
  assert.equal(requests.length, 0);
});
