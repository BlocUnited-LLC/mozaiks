import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';
import { inspectBootstrapFailure, observeBootstrap } from '../../examples/mobile-reference/scripts/acceptance-diagnostics.mjs';

test('bootstrap observation records only finite metadata and excludes URL credentials and error bodies', () => {
  const page = new EventEmitter();
  const frame = { url: () => 'https://localhost/login?code=private-code&state=private-state#private-fragment' };
  page.mainFrame = () => frame;
  const evidence = observeBootstrap(page, { backendOrigin: 'http://127.0.0.1:18443', webviewOrigin: 'https://localhost' });
  const request = { url: () => 'http://127.0.0.1:18443/api/shell-config?token=private-token' };
  page.emit('request', request);
  page.emit('response', { request: () => request, status: () => 200 });
  page.emit('requestfinished', request);
  page.emit('framenavigated', frame);
  assert.deepEqual(evidence, {
    shell_config: [{ sequence: 1, response_status: 200, finished: true, failed: false }],
    navigations: [{ bundled_origin: true, route: '/login' }],
  });
  assert.doesNotMatch(JSON.stringify(evidence), /private|token|127\.0|https?:/);
});

test('unrelated origins, invalid URLs and child frames are ignored; pending and failed bootstrap are distinguishable', () => {
  const page = new EventEmitter();
  const frame = { url: () => 'https://elsewhere.invalid/secret-path?secret=value' };
  page.mainFrame = () => frame;
  const evidence = observeBootstrap(page, { backendOrigin: 'http://127.0.0.1:18443', webviewOrigin: 'https://localhost' });
  for (const url of ['not-a-url', 'https://elsewhere.invalid/api/shell-config', 'http://127.0.0.1:18443/secret-route']) {
    page.emit('request', { url: () => url });
  }
  page.emit('framenavigated', { url: () => 'https://localhost/login' });
  page.emit('framenavigated', frame);
  const failed = { url: () => 'http://127.0.0.1:18443/api/shell-config' };
  const pending = { url: failed.url };
  page.emit('request', failed);
  page.emit('requestfailed', failed);
  page.emit('request', pending);
  assert.deepEqual(evidence.shell_config.map(entry => entry.failed), [true, false]);
  assert.deepEqual(evidence.navigations, [{ bundled_origin: false, route: 'other' }]);
  assert.doesNotMatch(JSON.stringify(evidence), /secret|elsewhere/);
});

test('bootstrap observations are capped', () => {
  const page = new EventEmitter();
  const frame = { url: () => 'https://localhost/community' };
  page.mainFrame = () => frame;
  const evidence = observeBootstrap(page, { backendOrigin: 'http://127.0.0.1:18443', webviewOrigin: 'https://localhost' });
  for (let count = 0; count < 30; count++) {
    page.emit('request', { url: () => 'http://127.0.0.1:18443/api/shell-config' });
    page.emit('framenavigated', frame);
  }
  assert.equal(evidence.shell_config.length, 12);
  assert.equal(evidence.navigations.length, 12);
});

test('failure inspection performs only a read-only native state probe and discards extra native data', async () => {
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  try {
    globalThis.document = { readyState: 'complete' };
    globalThis.window = { Capacitor: { getPlatform: () => 'android', Plugins: { App: { async getState() { return { isActive: true, token: 'private' }; } } } } };
    const result = await inspectBootstrapFailure();
    assert.equal(result.native_state_probe, 'active');
    assert.equal(result.document_ready_state, 'complete');
    assert.equal(result.native_platform, true);
    assert.doesNotMatch(JSON.stringify(result), /private|token/);
    globalThis.window.Capacitor.Plugins.App.getState = async () => { throw new Error('private error'); };
    assert.equal((await inspectBootstrapFailure()).native_state_probe, 'rejected');
    globalThis.window = {};
    assert.equal((await inspectBootstrapFailure()).native_state_probe, 'unavailable');
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
  }
});
