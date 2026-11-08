import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);

async function fixture(t, { strict = false, holdRegistry = false, holdDetails = false } = {}) {
  const state = { holdRegistry, holdDetails, failOverview: false, revision: 1 };
  const requests = [];
  const pending = [];
  const bundle = await build({
    stdin: { contents: `
      import React, {useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import {useAppStudioData} from ${JSON.stringify(path.join(root, 'factory_app/app/admin/pages/useAppStudioData.js'))};
      window.mozaiksAuth = {getAccessToken: () => 'fixture-token'};
      window.calls = [];
      const nativeFetch = window.fetch.bind(window);
      window.fetch = async (url, options = {}) => {
        const call = {url, aborted: false, settled: false};
        window.calls.push(call);
        options.signal?.addEventListener('abort', () => { call.aborted = true; });
        try { return await nativeFetch(url, options); }
        finally { call.settled = true; }
      };
      function Data({appId}) {
        const {data, loading, error, refresh} = useAppStudioData(appId);
        return <>
          <button onClick={refresh}>Refresh</button>
          <output data-testid="state">{JSON.stringify({loading, error, app: data?.summary?.app?.app_id, revision: data?.summary?.revision})}</output>
        </>;
      }
      function App() {
        const [appId, setAppId] = useState('app-a');
        const [mounted, setMounted] = useState(true);
        return <>
          <button onClick={() => setAppId('app-b')}>Open B</button>
          <button onClick={() => setMounted(false)}>Leave</button>
          {mounted ? <Data appId={appId}/> : <p>Left</p>}
        </>;
      }
      createRoot(document.getElementById('root')).render(${strict ? '<React.StrictMode><App/></React.StrictMode>' : '<App/>'});
    `, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, jsx: 'automatic', format: 'esm',
    nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"development"', 'import.meta.env': '{}' },
  });
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (!url.pathname.startsWith('/api/')) {
      res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
      res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text
        : '<!doctype html><html><body><div id="root"></div><script type="module" src="/fixture.js"></script></body></html>');
      return;
    }
    const app = url.searchParams.get('app_id') || url.searchParams.get('build_registry_id')?.replace('registry-', 'app-')
      || (url.pathname.includes('/app-b/') ? 'app-b' : 'app-a');
    const request = { path: url.pathname, app, auth: req.headers.authorization, aborted: false };
    requests.push(request);
    res.on('close', () => { if (!res.writableFinished) request.aborted = true; });
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Cache-Control', 'no-store');
    if (url.pathname === '/api/studio/overview' && state.failOverview) res.statusCode = 503;
    const reply = () => {
      if (res.destroyed) return;
      if (url.pathname === '/api/studio/apps') {
        res.end(JSON.stringify({ apps: ['a', 'b'].map(id => ({ app_id: `app-${id}`, build_registry_id: `registry-${id}` })) }));
      } else if (url.pathname === '/api/studio/overview') {
        res.end(JSON.stringify({ app: { app_id: app }, revision: state.revision }));
      } else res.end('{}');
    };
    if ((url.pathname === '/api/studio/apps' && state.holdRegistry)
      || (url.pathname !== '/api/studio/apps' && app === 'app-a' && state.holdDetails)) {
      res.flushHeaders();
      pending.push(reply);
    }
    else reply();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { pending.forEach(reply => reply()); server.closeAllConnections(); return new Promise(resolve => server.close(resolve)); });
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const waitForData = async (app, revision = 1) => page.waitForFunction(({app, revision}) => {
    const value = JSON.parse(document.querySelector('[data-testid="state"]').textContent);
    return !value.loading && !value.error && value.app === app && value.revision === revision;
  }, {app, revision}).catch(async error => {
    throw new Error(`${error.message}: ${await page.getByTestId('state').textContent()}; requests=${JSON.stringify(requests)}`);
  });
  return { page, state, requests, pending, waitForData };
}

test('StrictMode sends one detail batch and explicit refresh fetches fresh data', async t => {
  const {page, state, requests, waitForData} = await fixture(t, {strict: true});
  await waitForData('app-a');
  await page.waitForFunction(() => window.calls.every(call => call.settled));
  const details = requests.filter(request => request.path !== '/api/studio/apps');
  assert.equal(details.length, 10);
  assert.equal(new Set(details.map(request => request.path)).size, 10);
  assert.ok(requests.length <= 12, 'an abandoned registry request must not start another batch');
  const calls = await page.evaluate(() => window.calls);
  assert.equal(calls.filter(call => call.url === '/api/studio/apps').length, 2, 'exercise the real development StrictMode replay');
  assert.equal(calls[0].aborted, true);
  assert.ok(requests.every(request => request.auth === 'Bearer fixture-token'));
  const before = requests.length;
  state.revision = 2;
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await waitForData('app-a', 2);
  assert.equal(requests.length - before, 11);
});

test('navigation aborts a pending registry lookup before it can start an old app batch', async t => {
  const {page, state, requests, pending, waitForData} = await fixture(t, {holdRegistry: true});
  await page.waitForFunction(() => window.calls.length === 1);
  state.holdRegistry = false;
  await page.getByRole('button', {name: 'Open B', exact: true}).click();
  await waitForData('app-b');
  pending.forEach(reply => reply());
  await page.waitForFunction(() => window.calls.every(call => call.settled));
  assert.equal(await page.evaluate(() => window.calls[0].aborted), true);
  assert.equal(requests.filter(request => request.path === '/api/studio/overview' && request.app === 'app-a').length, 0);
  assert.equal(requests.filter(request => request.path === '/api/studio/overview' && request.app === 'app-b').length, 1);
});

test('navigation cancels pending details and unmount cancels the next registry lookup', async t => {
  const {page, state, pending, waitForData} = await fixture(t, {holdDetails: true});
  await page.waitForFunction(() => window.calls.length === 11);
  state.holdDetails = false;
  await page.getByRole('button', {name: 'Open B', exact: true}).click();
  await waitForData('app-b');
  pending.forEach(reply => reply());
  await page.waitForFunction(() => window.calls.every(call => call.settled));
  assert.ok(await page.evaluate(() => window.calls.slice(1, 11).every(call => call.aborted)));
  await waitForData('app-b');
  state.holdRegistry = true;
  const count = await page.evaluate(() => window.calls.length);
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await page.waitForFunction(count => window.calls.length > count, count);
  await page.getByRole('button', {name: 'Leave', exact: true}).click();
  await page.getByText('Left', {exact: true}).waitFor();
  assert.equal(await page.evaluate(() => window.calls.at(-1).aborted), true);
  assert.equal(await page.evaluate(() => window.calls.length), count + 1);
});

test('a current request failure remains visible and explicit refresh can recover', async t => {
  const {page, state, waitForData} = await fixture(t);
  await waitForData('app-a');
  state.failOverview = true;
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await page.waitForFunction(() => JSON.parse(document.querySelector('[data-testid="state"]').textContent).error === 'App summary unavailable.');
  state.failOverview = false;
  state.revision = 2;
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await waitForData('app-a', 2);
});
