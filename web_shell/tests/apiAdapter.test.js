import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const apiSource = await readFile(new URL('../../chat-ui/src/adapters/api.js', import.meta.url), 'utf8');
const notificationSource = await readFile(new URL('../../chat-ui/src/components/layout/notificationApi.js', import.meta.url), 'utf8');
const withoutImports = source => source.replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');

// Execute the production module with only its environment/configuration imports
// supplied by the fixture. No frontend dependencies or network are needed.
function fixture({ baseUrl = 'https://runtime.example', platformBase = baseUrl, origin = 'https://localhost' } = {}) {
  const requests = [];
  const response = { ok: true, status: 200, headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ({ count: 3 }) };
  let token = 'current-session';
  let tokenReads = 0;
  const platform = {
    resolveHttpUrl: () => platformBase,
    getAccessToken: () => { tokenReads += 1; return token; },
  };
  const context = vm.createContext({
    URL, URLSearchParams, Request, Headers, console, platform,
    resolveWorkflow: value => value,
    window: { location: new URL(origin) },
    config: { get: key => key === 'api.baseUrl' ? baseUrl : undefined },
    fetch: async (input, options) => { requests.push({ input, options }); return response; },
  });
  const api = vm.runInContext(`${withoutImports(apiSource)}\n({ authFetch, ApiAdapter, RestApiAdapter });`, context);
  context.authFetch = api.authFetch;
  const notifications = vm.runInContext(`${withoutImports(notificationSource)}\n({ fetchNotificationCount, clearNotifications });`, context);
  return {
    ...api, ...notifications, requests, response,
    setToken(value) { token = value; },
    tokenReads: () => tokenReads,
  };
}

test('REST workflow input preserves the server delivery outcome on refusal', async () => {
  const f = fixture();
  f.response.ok = false;
  f.response.status = 409;
  f.response.json = async () => ({ delivery_state: 'refused' });
  const refused = await new f.RestApiAdapter().sendMessageToWorkflow('Hello', 'app-1', 'user-1', 'AskAgent', 'chat-1');
  assert.equal(refused.success, false);
  assert.equal(refused.delivery_state, 'refused');

  f.response.status = 503;
  f.response.json = async () => ({ delivery_state: 'unknown' });
  const uncertain = await new f.RestApiAdapter().sendMessageToWorkflow('Hello', 'app-1', 'user-1', 'AskAgent', 'chat-1');
  assert.equal(uncertain.success, false);
  assert.equal(uncertain.delivery_state, 'unknown');
});

test('bundled client backend requests retain the configured prefix and query', async () => {
  const f = fixture({ baseUrl: 'https://runtime.example/gateway/' });
  const options = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"name":"post"}' };
  assert.equal(await f.authFetch('/api/modules/posts/create?draft=true', options), f.response);
  assert.equal(f.requests[0].input, 'https://runtime.example/gateway/api/modules/posts/create?draft=true');
  assert.equal(f.requests[0].options.headers.get('authorization'), 'Bearer current-session');
  assert.equal(f.requests[0].options.body, options.body);
  assert.equal(f.requests[0].options.method, 'POST');
  assert.deepEqual(options.headers, { 'Content-Type': 'application/json' });
});

test('API methods and authFetch use the same configured backend precedence', async () => {
  const f = fixture({ baseUrl: 'https://shared.example', platformBase: 'https://platform.example' });
  const configuration = { baseUrl: 'https://explicit.example/prefix/', api: { baseUrl: 'https://nested.example' } };
  const api = new f.ApiAdapter(configuration);
  assert.equal(api.getHttpBaseUrl(), 'https://explicit.example/prefix');
  await api.get('api/pages/home');
  await api.delete('/api/notifications');
  assert.equal(f.requests[0].input, 'https://explicit.example/prefix/api/pages/home');
  assert.equal(f.requests[1].input, 'https://explicit.example/prefix/api/notifications');
  assert.equal(f.requests[1].options.method, 'DELETE');
  await f.authFetch('/api/me', {}, { api: configuration.api });
  assert.equal(f.requests[2].input, 'https://nested.example/api/me');
  await f.authFetch('/api/me');
  assert.equal(f.requests[3].input, 'https://shared.example/api/me');
  assert.ok(f.requests.every(request => request.options.headers.get('authorization') === 'Bearer current-session'));
});

test('the platform bridge supplies the backend when shared configuration is absent', async () => {
  const f = fixture({ baseUrl: null, platformBase: 'https://platform.example' });
  await f.authFetch('/api/me');
  assert.equal(f.requests[0].input, 'https://platform.example/api/me');
  assert.equal(f.requests[0].options.headers.get('authorization'), 'Bearer current-session');
});

test('an explicit empty base preserves the browser proxy origin', async () => {
  const f = fixture({ origin: 'https://app.example' });
  const api = new f.ApiAdapter({ baseUrl: '' });
  assert.equal(api.getHttpBaseUrl(), '');
  await api.get('/api/me');
  assert.equal(f.requests[0].input, '/api/me');
  assert.equal(f.requests[0].options.headers.get('authorization'), 'Bearer current-session');
  await f.authFetch('https://runtime.example/api/me', {}, { baseUrl: '' });
  assert.equal(f.requests[1].options.headers.get('authorization'), null);
});

test('relative proxy bases are not added twice to adapter-composed paths', async () => {
  const f = fixture({ baseUrl: '/gateway/' });
  const api = new f.ApiAdapter();
  await api.get('/api/me');
  await api.listGeneralChats('app', 'user');
  assert.equal(f.requests[0].input, '/gateway/api/me');
  assert.equal(f.requests[1].input, '/gateway/api/general_chats/list/app/user?limit=50');
  assert.ok(f.requests.every(request => request.options.headers.get('authorization') === 'Bearer current-session'));
});

for (const destination of [
  'https://external.example/api/me',
  'https://runtime.example.attacker.example/api/me',
  'https://runtime.example@external.example/api/me',
  'https://runtime.example:8443/api/me',
  'http://runtime.example/api/me',
  '//external.example/api/me',
  '/\\external.example/api/me',
  'https://localhost/api/me',
  'data:text/plain,hello',
  'blob:https://runtime.example/identifier',
]) {
  test(`does not read or attach the session token for ${destination}`, async () => {
    const f = fixture();
    await f.authFetch(destination);
    assert.equal(f.requests[0].input, destination);
    assert.equal(f.requests[0].options.headers.get('authorization'), null);
    assert.equal(f.tokenReads(), 0);
  });
}

test('a backend URL object retains its destination and receives the current token', async () => {
  const f = fixture();
  const url = new URL('https://runtime.example/api/me?detail=full');
  await f.authFetch(url);
  assert.equal(f.requests[0].input, url);
  assert.equal(f.requests[0].options.headers.get('authorization'), 'Bearer current-session');
});

test('external URL and Request objects receive no automatic session token', async () => {
  const f = fixture();
  const url = new URL('https://external.example/api/me');
  const request = new Request(url, { headers: { 'X-Caller': 'request' } });
  await f.authFetch(url);
  await f.authFetch(request);
  assert.equal(f.requests[0].input, url);
  assert.equal(f.requests[1].input, request);
  assert.equal(f.requests[1].options.headers.get('x-caller'), 'request');
  assert.ok(f.requests.every(call => !call.options.headers.has('authorization')));
  assert.equal(f.tokenReads(), 0);
});

for (const [kind, headers] of [
  ['object', { authorization: 'Basic caller-credential', 'X-Caller': 'preserved' }],
  ['Headers', new Headers({ authorization: 'Basic caller-credential', 'X-Caller': 'preserved' })],
  ['tuples', [['authorization', 'Basic caller-credential'], ['X-Caller', 'preserved']]],
]) {
  test(`preserves explicit authorization and caller headers supplied as ${kind}`, async () => {
    const f = fixture();
    await f.authFetch('/api/me', { headers });
    assert.equal(f.requests[0].options.headers.get('authorization'), 'Basic caller-credential');
    assert.equal(f.requests[0].options.headers.get('x-caller'), 'preserved');
    assert.equal(f.tokenReads(), 0);
    assert.equal(new Headers(headers).get('authorization'), 'Basic caller-credential');
  });
}

test('an explicit external credential is preserved without reading the application session', async () => {
  const f = fixture();
  await f.authFetch('https://external.example/api', { headers: { Authorization: 'Basic external' } });
  assert.equal(f.requests[0].options.headers.get('authorization'), 'Basic external');
  assert.equal(f.tokenReads(), 0);
});

test('Request body, headers, signal and options survive automatic authorization', async () => {
  const f = fixture();
  const controller = new AbortController();
  const request = new Request('https://runtime.example/api/modules/posts/create', {
    method: 'POST', body: '{"text":"hello"}', signal: controller.signal,
    headers: { 'Content-Type': 'application/json', 'X-Caller': 'original' },
    credentials: 'include', redirect: 'error', cache: 'no-store',
  });
  await f.authFetch(request);
  const call = f.requests[0];
  assert.equal(call.input, request);
  assert.equal(request.bodyUsed, false);
  assert.equal(request.headers.get('authorization'), null);
  const effective = new Request(call.input, call.options);
  assert.equal(await effective.text(), '{"text":"hello"}');
  assert.equal(effective.method, 'POST');
  assert.equal(effective.headers.get('x-caller'), 'original');
  assert.equal(effective.headers.get('authorization'), 'Bearer current-session');
  assert.equal(effective.credentials, 'include');
  assert.equal(effective.redirect, 'error');
  assert.equal(effective.cache, 'no-store');
  controller.abort();
  assert.equal(effective.signal.aborted, true);
});

test('Request authorization survives, while explicit init headers replace Request headers', async () => {
  const f = fixture();
  const request = new Request('https://runtime.example/api/me', {
    headers: { authorization: 'Bearer request-token', 'X-Request': 'original' },
  });
  await f.authFetch(request);
  assert.equal(f.requests[0].options.headers.get('authorization'), 'Bearer request-token');
  await f.authFetch(request, { headers: { 'X-Init': 'replacement' }, cache: 'reload' });
  assert.equal(f.requests[1].options.headers.get('x-request'), null);
  assert.equal(f.requests[1].options.headers.get('x-init'), 'replacement');
  assert.equal(f.requests[1].options.headers.get('authorization'), 'Bearer current-session');
  assert.equal(f.requests[1].options.cache, 'reload');
  assert.equal(request.headers.get('authorization'), 'Bearer request-token');
});

test('automatic authorization reads the current adapter session and does not revive a logged-out session', async () => {
  const f = fixture();
  let token = 'adapter-first';
  const adapter = { auth: { getAccessToken: () => token } };
  await f.authFetch('/api/me', {}, adapter);
  token = 'adapter-refreshed';
  await f.authFetch('/api/me', {}, adapter);
  token = null;
  await f.authFetch('/api/me', {}, adapter);
  assert.deepEqual(f.requests.map(call => call.options.headers.get('authorization')), [
    'Bearer adapter-first', 'Bearer adapter-refreshed', null,
  ]);
  assert.equal(f.tokenReads(), 0);
});

test('notification reads and deletion use the configured backend and preserve cancellation', async () => {
  const f = fixture();
  const controller = new AbortController();
  assert.deepEqual(await f.fetchNotificationCount({ signal: controller.signal }), { count: 3 });
  assert.equal(await f.clearNotifications(), f.response);
  assert.equal(f.requests[0].input, 'https://runtime.example/api/notifications/count');
  assert.equal(f.requests[0].options.signal, controller.signal);
  assert.equal(f.requests[1].input, 'https://runtime.example/api/notifications');
  assert.equal(f.requests[1].options.method, 'DELETE');
  assert.ok(f.requests.every(call => call.options.headers.get('authorization') === 'Bearer current-session'));
});

test('notifications make no request after logout', async () => {
  const f = fixture();
  f.setToken(null);
  assert.equal(await f.fetchNotificationCount(), null);
  assert.equal(await f.clearNotifications(), null);
  assert.equal(f.requests.length, 0);
});
