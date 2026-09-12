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
    jsx: 'automatic', loader: { '.js': 'jsx' }, nodePaths: [path.join(shell, 'node_modules')],
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
