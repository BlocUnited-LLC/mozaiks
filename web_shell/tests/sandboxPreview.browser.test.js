import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const hook = path.join(path.dirname(shell), 'factory_app/workflows/AppGenerator/ui/useSandbox.js');
const pane = path.join(path.dirname(shell), 'factory_app/workflows/AppGenerator/ui/PreviewPane.js');

test('draft preview preserves workspace branding, opens separately, and follows saved version lifecycle', async (t) => {
  const requests = [];
  let failStart = false;
  let delayedStart = null;
  let delayNextStart = false;
  let previewUrl;
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React, { useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import { useSandbox } from ${JSON.stringify(hook)};
      import PreviewPane from ${JSON.stringify(pane)};
      function Fixture() {
        const [version, setVersion] = useState(1);
        const preview = useSandbox('artifact-' + version, 'registry-a');
        return <main>
          <h1 id="workspace-brand" style={{color:'var(--color-primary)',fontFamily:'sans-serif'}}>Mozaiks builder</h1>
          <PreviewPane
            previewUrl={preview.livePreviewUrl}
            artifactVersionId={'artifact-' + version}
            sandboxStatus={preview.sandboxStatus}
            sandboxSyncing={preview.syncing}
            sandboxError={preview.sandboxError}
            onStartPreview={() => preview.syncAndRestart({'app.json':'{}'})}
            canStartPreview
          />
          <button onClick={() => setVersion(version + 1)}>Next version</button>
          <button onClick={() => window.previewSocket.onmessage({data:JSON.stringify({type:'status',status:'error',lastError:'Container expired'})})}>Expire</button>
          <output aria-label="Version">{version}</output>
          <output aria-label="State">{JSON.stringify({status:preview.sandboxStatus,url:preview.livePreviewUrl,error:preview.sandboxError,syncing:preview.syncing})}</output>
        </main>;
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    ` },
    bundle: true, write: false, jsx: 'automatic', loader: {'.js': 'jsx'}, nodePaths: [path.join(shell, 'node_modules')],
    plugins: [{ name: 'preview-transport-fixture', setup(builder) {
      builder.onResolve({filter: /websocketAuth\.js$/}, () => ({path: 'socket', namespace: 'fixture'}));
      builder.onResolve({filter: /studioApi\.js$/}, () => ({path: 'http', namespace: 'fixture'}));
      builder.onLoad({filter: /.*/, namespace: 'fixture'}, ({path: kind}) => ({contents: kind === 'socket'
        ? 'export function openAuthenticatedWebSocket() { const socket = {close(){}}; window.previewSocket = socket; return socket; }'
        : 'export const getStudioAccessToken = () => null; export const studioFetch = (...args) => fetch(...args);', loader: 'js'}));
    }}],
  });
  const server = http.createServer((req, res) => {
    if (req.url === '/fixture.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(bundle.outputFiles[0].text); return; }
    if (req.url === '/preview') {
      res.setHeader('Content-Type', 'text/html');
      res.end('<style>:root{--color-primary:#c2410c}body{font-family:serif;color:var(--color-primary)}</style><h1>Bakery</h1><button onclick="this.textContent=\'Added\'">Add item</button>');
      return;
    }
    if (!req.url.startsWith('/api/')) { res.setHeader('Content-Type', 'text/html'); res.end('<style>:root{--color-primary:#06b6d4}</style><div id="root"></div><script src="/fixture.js"></script>'); return; }
    requests.push(req.url);
    res.setHeader('Content-Type', 'application/json');
    if (req.url.includes('/artifacts/')) res.end(JSON.stringify({sandboxId: 'sandbox-' + new URL(req.url, 'http://local').pathname.split('/')[3]}));
    else if (req.url.endsWith('/start')) {
      const finish = () => res.end(JSON.stringify(failStart ? {status:'error',previewUrl:null,message:'Backend startup failed'} : {status:'running',previewUrl}));
      if (delayNextStart) { delayNextStart = false; delayedStart = finish; } else finish();
    } else if (req.url.endsWith('/status')) res.end(JSON.stringify({status:'running',previewUrl}));
    else res.end('{"ok":true}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  previewUrl = `http://127.0.0.1:${server.address().port}/preview`;
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const state = async () => JSON.parse(await page.getByLabel('State').textContent());
  await expect(page.getByText('Draft app preview', {exact:true})).toBeVisible();
  await expect(page.getByText('Temporary preview · Changes here do not publish your app.')).toBeVisible();
  await expect(page.getByText('Version artifact-1')).toBeVisible();
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.equal(requests[0], '/api/artifacts/artifact-1/sandbox?build_registry_id=registry-a');
  assert.equal((await state()).url, previewUrl);
  const frame = page.frameLocator('iframe[title="Draft app preview"]');
  await expect(frame.getByRole('heading', {name:'Bakery'})).toHaveCSS('color', 'rgb(194, 65, 12)');
  await expect(frame.getByRole('heading', {name:'Bakery'})).toHaveCSS('font-family', 'serif');
  await frame.getByRole('button', {name:'Add item'}).click();
  await expect(frame.getByRole('button', {name:'Added'})).toBeVisible();
  await expect(page.getByRole('heading', {name:'Mozaiks builder'})).toHaveCSS('color', 'rgb(6, 182, 212)');
  await expect(page.getByRole('heading', {name:'Mozaiks builder'})).toHaveCSS('font-family', 'sans-serif');
  const popupOpened = page.waitForEvent('popup');
  await page.getByRole('link', {name:'Open draft preview',exact:true}).click();
  const popup = await popupOpened;
  await expect(popup.getByRole('heading', {name:'Bakery'})).toBeVisible();
  assert.equal(popup.url(), previewUrl);
  assert.equal(await popup.evaluate(() => window.opener), null);
  await expect(page.getByRole('heading', {name:'Mozaiks builder'})).toBeVisible();
  await popup.close();
  await page.getByRole('button', {name:'Expire',exact:true}).click();
  await expect.poll(async () => (await state()).error).toBe('Container expired');
  assert.equal((await state()).url, null);
  await expect(page.locator('iframe')).toHaveCount(0);
  await expect(page.getByRole('link', {name:'Open draft preview',exact:true})).toHaveCount(0);
  failStart = true;
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).error).toBe('Backend startup failed');
  assert.equal((await state()).url, null);
  failStart = false;
  delayNextStart = true;
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(() => Boolean(delayedStart)).toBe(true);
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await expect(page.getByLabel('Version')).toHaveText('2');
  await expect(page.getByText('Version artifact-2')).toBeVisible();
  delayedStart();
  await expect.poll(async () => (await state()).syncing).toBe(false);
  assert.equal((await state()).url, null);
  assert.equal((await state()).status, null);
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.ok(requests.includes('/api/artifacts/artifact-2/sandbox?build_registry_id=registry-a'));
});

test('standalone shell identifies drafts through loading and navigation without blocking app controls', async (t) => {
  const bundles = {};
  for (const preview of [false, true]) {
    const bundle = await build({
      stdin: {resolveDir: shell, loader: 'jsx', contents: `
        import React from 'react';
        import { createRoot } from 'react-dom/client';
        import App from './App.jsx';
        createRoot(document.getElementById('root')).render(<App />);
      `},
      bundle: true, write: false, jsx: 'automatic', nodePaths: [path.join(shell, 'node_modules')],
      define: {'import.meta.env': JSON.stringify(preview ? {VITE_MOZAIKS_PREVIEW:'true'} : {})},
      plugins: [{name: 'shell-bootstrap-fixture', setup(builder) {
        builder.onResolve({filter: /^@mozaiks\/chat-ui$/}, () => ({path:'chat-ui', namespace:'fixture'}));
        builder.onResolve({filter: /^@platform\/extensions$/}, () => ({path:'extensions', namespace:'fixture'}));
        builder.onLoad({filter: /.*/, namespace:'fixture'}, ({path: kind}) => ({
          resolveDir: shell, loader:'jsx', contents: kind === 'extensions' ? 'export {};' : `
            import React, {useState} from 'react';
            export const componentRegistry = {hasComponent: () => true};
            export class WebSocketApiAdapter {}
            export const LoginPage = () => null;
            export const AuthCallbackPage = () => null;
            export const loadShellAuth = () => new Promise(resolve => {
              window.finishBootstrap = () => resolve({authAdapter:{}, shellConfig:{appName:'Bakery'}});
            });
            export function MozaiksApp() {
              const [page, setPage] = useState('Bakery');
              return <main style={{minHeight:'100vh', color:'rgb(194, 65, 12)', fontFamily:'serif'}}>
                <h1>{page}</h1>
                <button onClick={() => { history.pushState({}, '', '/orders'); setPage('Orders'); }}>Orders</button>
                <button id="edge-control" style={{position:'fixed',left:8,bottom:80,padding:8}}
                  onClick={() => setPage('Edge control worked')}>Save</button>
              </main>;
            }
          `,
        }));
      }}],
    });
    bundles[preview] = bundle.outputFiles[0].text;
  }
  const server = http.createServer((req, res) => {
    if (req.url.endsWith('.js')) {
      res.setHeader('Content-Type', 'text/javascript');
      res.end(bundles[req.url.includes('true')]);
      return;
    }
    res.setHeader('Content-Type', 'text/html');
    res.end(`<style>body{margin:0}</style><div id="root"></div><script src="/${req.url.includes('true')}.js"></script>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => {server.closeAllConnections(); server.close(resolve);}));
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  for (const preview of [false, true]) {
    for (const width of [1280, 390]) {
      await t.test(`${preview ? 'draft' : 'normal'} launch at ${width}px`, async () => {
        const page = await browser.newPage({viewport:{width,height:800}});
        await page.goto(`http://127.0.0.1:${server.address().port}/${preview}`);
        const badge = page.getByRole('note', {name:'Draft app preview'});
        await expect(page.getByRole('status')).toHaveText('Loading app…');
        await expect(badge).toHaveCount(preview ? 1 : 0);
        await page.evaluate(() => window.finishBootstrap());
        await expect(page.getByRole('heading', {name:'Bakery'})).toHaveCSS('color', 'rgb(194, 65, 12)');
        await page.getByRole('button', {name:'Orders',exact:true}).click();
        await expect(page.getByRole('heading', {name:'Orders'})).toBeVisible();
        await expect(badge).toHaveCount(preview ? 1 : 0);
        if (preview) {
          await expect(badge).toBeVisible();
          const box = await badge.boundingBox();
          assert.ok(box.x >= 0 && box.x + box.width <= width);
          assert.ok(box.y >= 0 && box.y + box.height <= 800);
          await expect(badge).toHaveCSS('pointer-events', 'none');
        }
        // Click the actual point underneath the overlay, not a forced DOM click.
        const control = await page.locator('#edge-control').boundingBox();
        await page.mouse.click(control.x + 10, control.y + 10);
        await expect(page.getByRole('heading', {name:'Edge control worked'})).toBeVisible();
        await expect(badge).toHaveCount(preview ? 1 : 0);
        await page.close();
      });
    }
  }
});
