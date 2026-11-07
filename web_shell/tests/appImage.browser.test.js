import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const component = path.join(path.dirname(shell), 'chat-ui/src/ui/primitives/Image.jsx');
const platformModule = path.join(path.dirname(shell), 'chat-ui/src/platform/index.js');
const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV1kAAAAASUVORK5CYII=', 'base64');

for (const { name, baseUrl, expectedBase } of [
  { name: 'configured backend prefix', baseUrl: 'http://backend.test/gateway/', expectedBase: 'http://backend.test/gateway' },
  { name: 'explicit same-origin proxy', baseUrl: '', expectedBase: 'http://frontend.test' },
  { name: 'platform backend', baseUrl: null, expectedBase: 'http://platform.test' },
]) {
  test(`Image uses ${name} and retries after its asset changes`, async (t) => {
    const bundle = await build({
      stdin: { contents: `
        import React, {useState} from 'react';
        import {createRoot} from 'react-dom/client';
        import {Image} from ${JSON.stringify(component)};
        import {configurePlatform} from ${JSON.stringify(platformModule)};
        configurePlatform({getBaseUrls: () => ({httpUrl: 'http://platform.test', wsUrl: 'ws://platform.test'})});
        function Fixture() {
          const [asset, setAsset] = useState('missing');
          window.replaceAsset = setAsset;
          return <Image asset_id={asset} alt="App hero" caption="Hero caption" />;
        }
        createRoot(document.getElementById('root')).render(<Fixture/>);
      `, resolveDir: shell, loader: 'jsx' },
      bundle: true, write: false, jsx: 'automatic',
      nodePaths: [path.join(shell, 'node_modules')],
      alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
      define: { 'process.env': '{}' },
      logLevel: 'error',
    });
    const browser = await chromium.launch({ headless: true });
    t.after(() => browser.close());
    const page = await browser.newPage();
    const requests = [];
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(value => { window.ChatUIConfig = { api: { baseUrl: value } }; }, baseUrl);
    await page.route('http://**/*', route => {
      const url = route.request().url();
      if (url.includes('/api/app-assets/')) {
        requests.push(url);
        return url.endsWith('/hero')
          ? route.fulfill({ contentType: 'image/png', body: pixel })
          : route.fulfill({ status: 404, body: 'Asset not available' });
      }
      return route.fulfill({ contentType: 'text/html', body: '<div id="root"></div>' });
    });
    await page.goto('http://frontend.test');
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.getByText('Image coming soon', { exact: true }).waitFor();
    assert.deepEqual(requests, [`${expectedBase}/api/app-assets/missing`]);

    await page.evaluate(() => window.replaceAsset('hero'));
    await page.waitForFunction(() => document.querySelector('figure img')?.naturalWidth === 1);
    assert.deepEqual(requests, [
      `${expectedBase}/api/app-assets/missing`,
      `${expectedBase}/api/app-assets/hero`,
    ]);
    assert.equal(await page.getByAltText('App hero').count(), 1);
    assert.equal(await page.locator('figcaption').innerText(), 'Hero caption');
    assert.deepEqual(errors, []);
  });
}
