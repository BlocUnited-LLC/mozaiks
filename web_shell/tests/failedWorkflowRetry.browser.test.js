import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { build, transform } from 'esbuild';
import { chromium } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);
const ui = path.join(root, 'chat-ui/src');

for (const scenario of [
  { name: 'failed session with stale loading', failed: true, loading: true, output: true, expected: false },
  { name: 'failed session with tool output only', failed: true, loading: false, output: true, expected: false },
  { name: 'retry launching', failed: true, launching: true, loading: true, output: true, expected: false },
  { name: 'active session loading', loading: true, expected: true },
  { name: 'active session awaiting first agent text', output: true, expected: true },
  { name: 'idle session', expected: false },
]) {
  test(`typing indicator: ${scenario.name}`, async () => {
    const source = await fs.readFile(path.join(ui, 'components/chat/ChatInterface.jsx'), 'utf8');
    const start = source.indexOf('  const showTypingIndicator =');
    const expression = source.slice(start, source.indexOf('  const renderedMessages =', start));
    const visible = vm.runInNewContext(`${expression}\nshowTypingIndicator;`, {
      loading: Boolean(scenario.loading), connectionStatus: 'connected', conversationMode: 'workflow',
      messages: scenario.output ? [{ metadata: { event_type: 'tool_progress' } }] : [],
      failedWorkflowRetry: scenario.failed ? { available: true, launching: Boolean(scenario.launching) } : null,
    });
    assert.equal(visible, scenario.expected);
  });
}

async function metadataHarness() {
  const source = (await fs.readFile(path.join(ui, 'pages/ChatPage.js'), 'utf8')).replaceAll('\r\n', '\n');
  const start = source.indexOf('  const hydrateServerArtifactForChat =');
  const callback = source.slice(start, source.indexOf('\n\n  useEffect(', start));
  const requests = [];
  const observed = [];
  const state = {
    useCallback: fn => fn, currentAppId: 'execution-host', currentUserId: 'operator',
    currentChatId: 'failed-chat', currentWorkflowName: 'ExampleWorkflow',
    token: 'initial-token', resolveKnownWorkflowName: value => value,
    chatMetaHydratedRef: { current: new Set() },
    chatMetaHydrationInFlightRef: { current: new Map() },
    chatMetaHydrationMissedAtRef: { current: new Map() },
    observeSessionMeta: meta => observed.push(meta), isFailedWorkflowSession: status => status === 2,
    setLoading() {}, setCacheSeed() {}, setStoredChatCacheSeed() {}, setChatExists() {},
    logChatPersistence() {}, cacheServerLastArtifact: () => true,
    console: { warn() {} },
  };
  state.api = { get: url => new Promise(resolve => requests.push({ url, token: state.token, resolve })) };
  const hydrate = vm.runInNewContext(`${callback}\nhydrateServerArtifactForChat;`, state);
  const meta = status => ({ exists: true, status, chat_id: 'failed-chat', app_id: 'execution-host',
    workflow_name: 'ExampleWorkflow', last_artifact: { tool_name: 'ExistingReview' } });
  return { state, requests, observed, hydrate, meta };
}

test('forced failure metadata bypasses an already hydrated artifact cache', async () => {
  const { requests, observed, hydrate, meta } = await metadataHarness();
  const initial = hydrate();
  requests[0].resolve(meta(0));
  await initial;
  assert.equal(await hydrate(), false);
  const forced = hydrate({ force: true, reason: 'workflow_failed' });
  assert.equal(requests.length, 2);
  requests[1].resolve(meta(2));
  await forced;
  assert.deepEqual(observed.map(value => value.status), [0, 2]);
});

for (const olderFirst of [false, true]) {
  test(`forced failure metadata supersedes an in-flight read across token refresh (olderFirst=${olderFirst})`, async () => {
    const { state, requests, observed, hydrate, meta } = await metadataHarness();
    const initial = hydrate();
    state.token = 'refreshed-token';
    const forced = hydrate({ force: true, reason: 'workflow_failed' });
    assert.equal(requests.length, 2, 'failure refresh must not be dropped behind a stale request');
    assert.deepEqual(requests.map(request => request.token), ['initial-token', 'refreshed-token']);
    if (olderFirst) {
      requests[0].resolve(meta(0));
      await initial;
      assert.equal(state.chatMetaHydrationInFlightRef.current.size, 1);
    }
    requests[1].resolve(meta(2));
    await forced;
    if (!olderFirst) { requests[0].resolve(meta(0)); await initial; }
    assert.deepEqual(observed.map(value => value.status), [2]);
    assert.equal(state.chatMetaHydrationInFlightRef.current.size, 0);
  });
}

test('ChatPage observes persisted status on reopen and refreshes it after terminal failure', async () => {
  const source = await fs.readFile(path.join(ui, 'pages/ChatPage.js'), 'utf8');
  await transform(source, { loader: 'jsx' });
  assert.match(source, /surface: navContext\?\.surface/);
  assert.match(source, /blocked: Boolean\(pendingTransitionId \|\| pendingHarnessDecision \|\| pendingWorkflowReply\)/);
  const hydration = source.split('const hydrateServerArtifactForChat =')[1].split('const handleIncomingRef =')[0];
  assert.match(hydration, /await api\.get\(`\/api\/chats\/meta\//);
  assert.match(hydration, /observeSessionMeta\(meta\)/);
  const completion = source.split("case 'run_complete':")[1].split("case 'chat.revision_requested':")[0];
  assert.match(completion, /hydrateServerArtifactForChat\(/);
  assert.match(completion, /force: true, reason: 'workflow_failed'/);
  assert.equal((source.match(/failedWorkflowRetry=\{failedWorkflowRetry.available \? failedWorkflowRetry : null\}/g) || []).length, 2);
});

test('failed workflow retry uses the existing authenticated launch path', async (t) => {
  const api = await fs.readFile(path.join(ui, 'adapters/api.js'), 'utf8');
  const authHelpers = api.slice(api.indexOf('function getAccessToken('), api.indexOf('export class ApiAdapter'));
  const stubs = {
    '../context/ChatUIContext': `export const useChatUI = () => ({
      user: { id: 'operator', app_id: 'wrong-token-default' }, config: { appId: 'wrong-config-default' },
      auth: { getAccessToken: () => window.fixture.token },
    });`,
    '../adapters/api': `const platform = { getAccessToken: () => { throw Error('unexpected token fallback'); } }; ${authHelpers}`,
    'react-router-dom': `export const useNavigate = () => window.fixture.navigate;
      export const useParams = () => ({});`,
    './ChatMessage': 'export default function ChatMessage() { return null; }',
    '../../core/ui/UIToolRenderer': 'export default function UIToolRenderer() { return null; }',
    '../../styles/brandAssets': `export const getBrandLogoSrc = () => '';
      export const applyBrandImageFallback = () => {};`,
    '../../session/chatSessionStorage': 'export const logChatPersistence = () => {};',
  };
  const entry = `
    import React, { useEffect, useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import { useFailedWorkflowRetry } from ${JSON.stringify(path.join(ui, 'hooks/useFailedWorkflowRetry.js'))};
    import ChatInterface from ${JSON.stringify(path.join(ui, 'components/chat/ChatInterface.jsx'))};
    window.fixture = {
      token: 'current-token', requests: [], navigations: [], next: { status: 200, body: { chat_id: 'fresh-chat', workflow_id: 'ResolvedWorkflow' } },
      navigate: (url) => window.fixture.navigations.push(url),
    };
    window.fetch = async (url, options) => {
      const fixture = window.fixture;
      fixture.requests.push({ url, method: options.method, headers: options.headers, body: JSON.parse(options.body) });
      await new Promise(resolve => { fixture.release = resolve; });
      if (fixture.next.network) throw Error('Network unavailable');
      return new Response(JSON.stringify(fixture.next.body), { status: fixture.next.status });
    };
    function Fixture() {
      const [scope, setScope] = useState({ appId: 'execution-host', userId: 'operator', chatId: 'failed-chat',
        workflowName: 'ExampleWorkflow', surface: 'studio', mode: 'workflow', blocked: false });
      const retry = useFailedWorkflowRetry(scope);
      useEffect(() => {
        window.fixture.scope = scope;
        window.fixture.setScope = (patch) => setScope(previous => ({ ...previous, ...patch }));
        window.fixture.observe = retry.observeSessionMeta;
        window.fixture.retry = retry.retry;
      });
      return <main style={{ height: '100vh', maxWidth: 960, margin: '0 auto', display: 'flex', flexDirection: 'column' }}>
        <ChatInterface messages={[]} onSendMessage={() => {}} workflowName={scope.workflowName}
          loading={false} connectionStatus="disconnected" conversationMode={scope.mode}
          hideHeader={true} plainContainer={true}
          failedWorkflowRetry={retry.available ? retry : null} />
      </main>;
    }
    createRoot(document.getElementById('root')).render(<Fixture />);
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' }, bundle: true, write: false,
    jsx: 'automatic', loader: { '.js': 'jsx' }, nodePaths: [path.join(shell, 'node_modules')],
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'mock-host-boundaries', setup(builder) {
      builder.onResolve({ filter: /.*/ }, (args) => (
        Object.hasOwn(stubs, args.path) ? { path: args.path, namespace: 'fixture-stub' } : undefined
      ));
      builder.onLoad({ filter: /.*/, namespace: 'fixture-stub' }, (args) => ({ contents: stubs[args.path], loader: 'js' }));
    } }],
  });
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell, 'styles.css'), 'utf8'))
      + `\n@source "${path.join(ui, 'components/chat/ChatInterface.jsx').replaceAll('\\', '/')}";`,
    { from: path.join(shell, 'styles.css') },
  );
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>${styles.css}
      :root {--color-text-primary:#172026;--color-primary-light:#0d7666;--color-error:#b91c1c;}
      body {margin:0;background:white;font-family:Arial;}
    </style></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>`;
  const server = http.createServer((req, res) => {
    if (req.url !== '/' && req.url !== '/fixture.js') { res.writeHead(404).end(); return; }
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  const errors = [];
  const unexpectedRequests = [];
  async function open(viewport = { width: 1280, height: 900 }) {
    const page = await browser.newPage({ viewport });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = route.request().url();
      if (url === `${baseUrl}/` || url === `${baseUrl}/fixture.js`) return route.continue();
      unexpectedRequests.push(url);
      return route.abort();
    });
    await page.goto(baseUrl);
    await page.waitForFunction(() => window.fixture?.observe);
    return page;
  }
  async function observe(page, patch = {}) {
    await page.evaluate(patch => window.fixture.observe({ exists: true, status: 2,
      chat_id: 'failed-chat', app_id: 'execution-host', workflow_name: 'ExampleWorkflow', ...patch }), patch);
  }
  const retryButton = page => page.getByRole('button', { name: 'Retry failed workflow', exact: true });

  for (const status of [0, 1, 'failed', 'paused', null]) {
    await t.test(`does not offer retry for unconfirmed terminal status ${status}`, async () => {
      const page = await open();
      await observe(page, { status });
      assert.equal(await retryButton(page).count(), 0);
      await page.close();
    });
  }
  for (const patch of [{ surface: 'platform' }, { surface: 'user' }, { surface: null }, { mode: 'ask' }, { blocked: true }]) {
    await t.test(`does not offer retry outside eligible Studio state ${JSON.stringify(patch)}`, async () => {
      const page = await open();
      await page.evaluate(patch => window.fixture.setScope(patch), patch);
      await observe(page);
      assert.equal(await retryButton(page).count(), 0);
      assert.equal(await page.evaluate(() => window.fixture.requests.length), 0);
      await page.close();
    });
  }
  for (const patch of [{ chat_id: 'other-chat' }, { app_id: 'other-host' }, { workflow_name: 'OtherWorkflow' }, { exists: false }]) {
    await t.test(`rejects mismatched or missing session metadata ${JSON.stringify(patch)}`, async () => {
      const page = await open();
      await observe(page, patch);
      assert.equal(await retryButton(page).count(), 0);
      await page.close();
    });
  }
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    await t.test(`submits once with selectors only and navigates after acknowledgement at ${viewport.width}px`, async () => {
      const page = await open(viewport);
      await observe(page);
      await retryButton(page).waitFor();
      assert.equal(await page.getByRole('textbox').isDisabled(), true);
      const screenshotDir = path.join(shell, 'test-results/failed-workflow-retry');
      await fs.mkdir(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, `${viewport.width}.png`), fullPage: true });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      const region = await page.getByRole('region', { name: 'Failed workflow' }).boundingBox();
      const button = await retryButton(page).boundingBox();
      assert.ok(button.x >= region.x && button.x + button.width <= region.x + region.width + 1);
      await page.evaluate(() => { window.fixture.retry(); window.fixture.retry(); });
      await page.waitForFunction(() => document.querySelector('[aria-label="Retry failed workflow"]').disabled);
      assert.deepEqual(await page.evaluate(() => window.fixture.requests), [{
        url: '/api/workflows/trigger', method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer current-token' },
        body: { trigger_source: 'manual', context_variables: {}, app_id: 'execution-host', user_id: 'operator',
          source_chat_id: 'failed-chat', retry_failed: true, workflow_id: 'ExampleWorkflow' },
      }]);
      assert.deepEqual(await page.evaluate(() => window.fixture.navigations), []);
      await page.evaluate(() => window.fixture.release());
      await page.waitForFunction(() => window.fixture.navigations.length === 1);
      const url = new URL(await page.evaluate(() => window.fixture.navigations[0]), baseUrl);
      assert.equal(url.pathname, '/chat');
      assert.deepEqual(Object.fromEntries(url.searchParams), {
        mode: 'workflow', workflow: 'ResolvedWorkflow', chat_id: 'fresh-chat', app_id: 'execution-host',
      });
      await page.evaluate(() => window.fixture.retry());
      assert.equal(await page.evaluate(() => window.fixture.requests.length), 1);
      await page.close();
    });
  }
  for (const outcome of [401, 403, 404, 500, 'network', 'missing_ack', 'old_chat', 'invalid_ack']) {
    await t.test(`launch failure ${outcome} keeps the failed session and allows an explicit retry`, async () => {
      const page = await open();
      await observe(page);
      await page.evaluate(outcome => {
        window.fixture.next = outcome === 'network' ? { network: true }
          : { status: typeof outcome === 'number' ? outcome : 200,
            body: outcome === 'old_chat' ? { chat_id: 'failed-chat', workflow_id: 'ExampleWorkflow' }
              : outcome === 'invalid_ack' ? { chat_id: {}, workflow_id: ['ExampleWorkflow'] }
              : { detail: 'Source build session is not available' } };
      }, outcome);
      await retryButton(page).click();
      await page.evaluate(() => window.fixture.release());
      await page.getByRole('alert').waitFor();
      assert.equal(await retryButton(page).isEnabled(), true);
      assert.deepEqual(await page.evaluate(() => window.fixture.navigations), []);
      assert.equal(await page.evaluate(() => window.fixture.scope.chatId), 'failed-chat');
      if ([401, 403, 404].includes(outcome)) assert.match(await page.getByRole('alert').textContent(), /Source build session is not available/);
      await page.evaluate(() => {
        window.fixture.token = 'refreshed-token';
        window.fixture.next = { status: 200, body: { chat_id: 'fresh-chat', workflow_id: 'ResolvedWorkflow' } };
      });
      await retryButton(page).click();
      assert.equal(await page.getByRole('alert').count(), 0);
      await page.evaluate(() => window.fixture.release());
      await page.waitForFunction(() => window.fixture.navigations.length === 1);
      assert.equal(await page.evaluate(() => window.fixture.requests[1].headers.Authorization), 'Bearer refreshed-token');
      await page.close();
    });
  }
  await t.test('token refresh during launch does not replay the request or lose its acknowledgement', async () => {
    const page = await open();
    await observe(page);
    await retryButton(page).click();
    await page.evaluate(() => { window.fixture.token = 'refreshed-token'; window.fixture.retry(); });
    assert.equal(await page.evaluate(() => window.fixture.requests.length), 1);
    assert.equal(await page.evaluate(() => window.fixture.requests[0].headers.Authorization), 'Bearer current-token');
    await page.evaluate(() => window.fixture.release());
    await page.waitForFunction(() => window.fixture.navigations.length === 1);
    await page.close();
  });
  for (const patch of [{ userId: 'other-operator', chatId: 'other-chat' }, { userId: 'other-operator' }, { mode: 'ask' }]) {
    await t.test(`scope change ignores stale callbacks and in-flight acknowledgement ${JSON.stringify(patch)}`, async () => {
      const page = await open();
      await observe(page);
      await retryButton(page).click();
      await page.evaluate(patch => {
        window.fixture.oldObserver = window.fixture.observe;
        window.fixture.oldRetry = window.fixture.retry;
        window.fixture.token = 'different-owner-token';
        window.fixture.setScope(patch);
      }, patch);
      await page.waitForFunction(patch => Object.entries(patch).every(([key, value]) => window.fixture.scope[key] === value), patch);
      await page.evaluate(() => {
        window.fixture.oldObserver({ exists: true, status: 2, chat_id: 'failed-chat',
          app_id: 'execution-host', workflow_name: 'ExampleWorkflow' });
        window.fixture.release();
      });
      await page.waitForTimeout(50);
      await page.evaluate(() => window.fixture.oldRetry());
      assert.equal(await retryButton(page).count(), 0);
      assert.deepEqual(await page.evaluate(() => window.fixture.navigations), []);
      assert.equal(await page.evaluate(() => window.fixture.requests.length), 1);
      await page.close();
    });
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpectedRequests, []);
});
