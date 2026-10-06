import assert from 'node:assert/strict';
import test from 'node:test';
import { setImmediate as tick } from 'node:timers/promises';
import { createNativeAuthorizationTransport } from '../../factory_app/build_context/mobile/templates/ui/auth/capacitor/authorizationTransport.mjs';

const callbackUri = 'org.mozaiks.examples.commonground:/auth/callback';
const request = { url: 'https://identity.example/authorize?state=one', callbackUri };
const callback = `${callbackUri}?state=one&code=example`;

function fixture(overrides = {}) {
  const listeners = new Map();
  const opened = [];
  const removed = [];
  async function addListener(name, listener) {
    listeners.set(name, listener);
    return { remove: async () => { removed.push(name); listeners.delete(name); } };
  }
  const App = { addListener, ...overrides.App };
  const Browser = { addListener, open: async value => { opened.push(value); }, ...overrides.Browser };
  return {
    transport: createNativeAuthorizationTransport({ App, Browser, timeoutMs: overrides.timeoutMs ?? 1000 }),
    emit: (name, value) => listeners.get(name)?.(value), opened, removed, listeners,
  };
}

test('opens the external browser after listeners and accepts only the exact callback', async () => {
  const f = fixture();
  const result = f.transport.open(request);
  await tick();
  assert.deepEqual(f.opened, [{ url: request.url }]);
  assert.equal(f.listeners.size, 2);
  let settled = false;
  result.then(() => { settled = true; });
  for (const url of [
    'https://example.org/auth/callback?state=one', `${callbackUri}/extra?state=one`,
    `${callbackUri}#code=example`, 'org.mozaiks.examples.commonground://host/auth/callback',
    'org.mozaiks.examples.commonground:/auth/../auth/callback?state=one',
  ]) f.emit('appUrlOpen', { url });
  f.emit('appUrlOpen', {});
  await tick();
  assert.equal(settled, false);
  f.emit('appUrlOpen', { url: callback });
  f.emit('appUrlOpen', { url: callback });
  assert.equal(await result, callback);
  assert.equal(f.listeners.size, 0);
  assert.deepEqual(f.removed.sort(), ['appUrlOpen', 'browserFinished']);
});

test('rejects overlapping opens without disturbing the active one', async () => {
  const f = fixture();
  const first = f.transport.open(request);
  await assert.rejects(f.transport.open(request), /already in progress/);
  await tick();
  f.emit('appUrlOpen', { url: callback });
  assert.equal(await first, callback);
  assert.equal(f.opened.length, 1);
});

test('browser cancellation removes listeners and permits a fresh attempt', async () => {
  const f = fixture();
  const failed = assert.rejects(f.transport.open(request), /cancelled/);
  await tick();
  f.emit('browserFinished');
  await failed;
  assert.equal(f.listeners.size, 0);
  const retried = f.transport.open(request);
  await tick();
  f.emit('appUrlOpen', { url: callback });
  assert.equal(await retried, callback);
  assert.equal(f.opened.length, 2);
});

test('browser open failure removes listeners and releases the active attempt', async () => {
  let attempts = 0;
  const f = fixture({ Browser: { open: async () => { attempts += 1; throw new Error('SDK failed'); } } });
  await assert.rejects(f.transport.open(request), /SDK failed/);
  await assert.rejects(f.transport.open(request), /SDK failed/);
  assert.equal(attempts, 2);
  assert.equal(f.listeners.size, 0);
});

test('a retained callback during listener registration completes without another browser open', async () => {
  let removed = 0;
  const f = fixture({ App: { addListener: async (_name, listener) => {
    listener({ url: callback });
    return { remove: async () => { removed += 1; } };
  } } });
  assert.equal(await f.transport.open(request), callback);
  assert.equal(f.opened.length, 0);
  assert.equal(removed, 1);
});

test('timeout rejects even when browser open IPC never settles', async () => {
  const f = fixture({ timeoutMs: 10, Browser: { open: () => new Promise(() => {}) } });
  await assert.rejects(f.transport.open(request), /expired/);
  assert.equal(f.listeners.size, 0);
});

test('timeout covers registration and removes a listener arriving after disposal', async () => {
  let resolveRegistration;
  let removed = 0;
  const f = fixture({ timeoutMs: 10, App: { addListener: () => new Promise(resolve => { resolveRegistration = resolve; }) } });
  await assert.rejects(f.transport.open(request), /expired/);
  resolveRegistration({ remove: async () => { removed += 1; } });
  await tick();
  assert.equal(removed, 1);
  assert.equal(f.opened.length, 0);
  assert.equal(f.listeners.size, 0);
});
