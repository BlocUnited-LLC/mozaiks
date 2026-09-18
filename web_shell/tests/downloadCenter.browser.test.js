import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('artifact download authenticates and only completes after a successful download', async (t) => {
  const requests = [];
  let status = 503;
  const entry = `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import DownloadCenter from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/core/ui/DownloadCenter.js'))};
    window.responses = [];
    createRoot(document.getElementById('root')).render(<DownloadCenter payload={{
      artifact_kind: 'workflow_bundle', artifact_version_id: 'av_1', build_registry_id: 'reg_1', app_id: 'factory',
      files: [{name: 'bundle.zip'}],
    }} onResponse={response => window.responses.push(response)} />);
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' }, bundle: true, write: false,
    jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' }, nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'authenticated-test-session', setup(build) {
      build.onResolve({ filter: /adapters\/api\.js$/ }, args => ({ path: args.path, namespace: 'auth-test' }));
      build.onLoad({ filter: /.*/, namespace: 'auth-test' }, () => ({
        contents: 'export const authFetch = (url, options) => fetch(url, {...options, headers: {Authorization:"Bearer test-session"}});',
      }));
    } }],
  });
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/api/')) {
      requests.push({ url: req.url, authorization: req.headers.authorization });
      res.statusCode = status; res.end('test archive'); return;
    }
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text : '<html><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByRole('button', { name: 'Download Bundle', exact: true }).click();
  await page.getByText('Some files failed to download: bundle.zip').waitFor();
  assert.deepEqual(await page.evaluate(() => window.responses), []);
  status = 200;
  const downloaded = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download Bundle', exact: true }).click();
  assert.equal((await downloaded).suggestedFilename(), 'bundle.zip');
  await page.waitForFunction(() => window.responses.length === 1);
  assert.equal(requests.length, 2);
  assert.ok(requests.every(request => request.authorization === 'Bearer test-session'));
  assert.ok(requests.every(request => request.url === '/api/studio/build/artifacts/av_1/download?build_registry_id=reg_1&app_id=factory'));
});

test('Continue displays rejection, prevents duplicate submissions, and awaits HTTP acceptance', async (t) => {
  const entry = `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import DownloadCenter from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/core/ui/DownloadCenter.js'))};
    import {submitToolCallResponse} from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/adapters/uiToolResponse.js'))};
    window.fixture = { requests: [], accepted: [], outcome: 403, token: 'test-session' };
    window.fetch = async (url, options) => {
      const fixture = window.fixture;
      fixture.requests.push({url, headers: options.headers, body: JSON.parse(options.body)});
      await new Promise(resolve => { fixture.release = resolve; });
      if (fixture.outcome === 'offline') throw Error('offline');
      return Response.json({status: fixture.outcome === 200 ? 'success' : 'rejected'}, {
        status: typeof fixture.outcome === 'number' ? fixture.outcome : 200,
      });
    };
    const respond = async response => {
      if (window.fixture.outcome === 'false') return false;
      await submitToolCallResponse('owned-event', response, {token: window.fixture.token});
      window.fixture.accepted.push(response);
      return true;
    };
    createRoot(document.getElementById('root')).render(<DownloadCenter payload={{
      files: [], actions: [{id: 'continue', label: 'Continue'}, {id: 'close', label: 'Close'}],
    }} onResponse={respond} />);
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' }, bundle: true, write: false,
    jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' }, nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'no-downloads', setup(build) {
      build.onResolve({ filter: /adapters\/api\.js$/ }, args => ({ path: args.path, namespace: 'auth-test' }));
      build.onLoad({ filter: /.*/, namespace: 'auth-test' }, () => ({
        contents: 'export const authFetch = () => { throw Error("unexpected download"); };',
      }));
    } }],
  });
  const server = http.createServer((req, res) => {
    const script = req.url === '/fixture.js';
    res.setHeader('Content-Type', script ? 'text/javascript' : 'text/html');
    res.end(script ? bundle.outputFiles[0].text : '<html><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const button = page.getByRole('button', { name: 'Continue', exact: true });
  for (const outcome of [403, 401, 404, 500, 'offline', 'unconfirmed']) {
    await page.evaluate(value => { window.fixture.outcome = value; }, outcome);
    const before = await page.evaluate(() => window.fixture.requests.length);
    await button.evaluate(element => { element.click(); element.click(); });
    await page.waitForFunction(count => window.fixture.requests.length > count, before);
    assert.equal(await page.evaluate(() => window.fixture.requests.length), before + 1);
    assert.equal(await page.getByRole('button', { name: 'Close', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('alert').count(), 0);
    assert.deepEqual(await page.evaluate(() => window.fixture.accepted), []);
    await page.evaluate(() => window.fixture.release());
    await page.getByRole('alert').waitFor();
    assert.match(await page.getByRole('alert').textContent(), /not accepted|no longer active|not confirm/);
    assert.equal(await button.isEnabled(), true);
  }
  await page.evaluate(() => { window.fixture.outcome = 'false'; });
  await button.click();
  await page.getByRole('alert').filter({ hasText: 'The server has not accepted this response.' }).waitFor();
  assert.deepEqual(await page.evaluate(() => window.fixture.accepted), []);

  await page.evaluate(() => { window.fixture.outcome = 200; window.fixture.token = 'refreshed-session'; });
  await button.click();
  assert.equal(await page.getByRole('alert').count(), 0);
  assert.deepEqual(await page.evaluate(() => window.fixture.accepted), []);
  await page.evaluate(() => window.fixture.release());
  await page.waitForFunction(() => window.fixture.accepted.length === 1);
  assert.deepEqual(await page.evaluate(() => window.fixture.accepted), [{status: 'submitted', action: 'continue', files: []}]);
  assert.deepEqual(await page.evaluate(() => window.fixture.requests.at(-1)), {
    url: '/api/tool-call/respond', headers: {'Content-Type': 'application/json', Authorization: 'Bearer refreshed-session'},
    body: { event_id: 'owned-event', response_data: { status: 'submitted', action: 'continue', files: [] } },
  });
  assert.deepEqual(errors, []);
});
