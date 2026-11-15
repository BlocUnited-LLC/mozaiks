import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';

test('Factory owner handoff uses native preview entry independently of draft login routes', async t => {
  const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const component = path.resolve(shell, '../factory_app/workflows/_shared/ui/app_preview/OwnerAccessCard.jsx');
  const bundle = await build({
    stdin: { contents: `import React from 'react'; import {createRoot} from 'react-dom/client';
      import Owner from ${JSON.stringify(component)};
      createRoot(document.getElementById('root')).render(<Owner {...window.scenario}/>);`, loader: 'jsx', resolveDir: shell },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
    nodePaths: [path.join(shell, 'node_modules')], define: { 'process.env.NODE_ENV': '"test"' },
  });
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text
      : '<main id="root"></main><script type="module" src="/fixture.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const origin = `http://127.0.0.1:${server.address().port}`;
  const files = {
    'app.json': JSON.stringify({ authRequired: true, admins: ['owner@example.test'] }),
    'ui/route_manifest.json': JSON.stringify({ pages: [{ path: '/sign-in', component: 'LoginPage' }] }),
  };
  for (const scenario of [
    { files, previewUrl: 'https://preview.example.test', href: 'https://preview.example.test/?owner=1' },
    { files, previewUrl: 'http://127.0.0.1:4312', href: 'http://127.0.0.1:4312/?owner=1' },
    { files, previewUrl: 'javascript:alert(1)' },
    { files, previewUrl: 'http://preview.example.test' },
    { files, previewUrl: 'https://user:credential@preview.example.test' },
    { files: { ...files, 'app.json': 'null' }, previewUrl: 'https://preview.example.test' },
    { files: { ...files, 'app.json': JSON.stringify({ authRequired: false, admins: ['owner@example.test'] }) }, previewUrl: 'https://preview.example.test' },
    { files: { ...files, 'ui/route_manifest.json': JSON.stringify({ pages: [{ path: '//other.example/sign-in', component: 'LoginPage' }] }) }, previewUrl: 'https://preview.example.test', href: 'https://preview.example.test/?owner=1' },
  ]) {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(value => { window.scenario = value; }, scenario);
    await page.goto(origin);
    await page.getByRole('heading', { name: 'Owner access', exact: true }).waitFor();
    const action = page.getByRole('link', { name: 'Open as owner', exact: true });
    await expect(action).toHaveCount(scenario.href ? 1 : 0);
    if (scenario.href) assert.equal(await action.getAttribute('href'), scenario.href);
    await page.getByText('Set up a self-hosted app', { exact: true }).click();
    await expect(page.getByText(/Create or invite the owner account/)).toBeVisible();
    assert.deepEqual(errors, []);
    await page.close();
  }
});
