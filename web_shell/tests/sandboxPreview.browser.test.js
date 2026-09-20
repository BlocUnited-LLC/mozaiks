import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const hook = path.join(path.dirname(shell), 'factory_app/workflows/AppGenerator/ui/useSandbox.js');

test('preview hook binds the saved version, clears failures, and ignores obsolete requests', async (t) => {
  const requests = [];
  let failStart = false;
  let delayedStart = null;
  let delayNextStart = false;
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React, { useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import { useSandbox } from ${JSON.stringify(hook)};
      function Fixture() {
        const [version, setVersion] = useState(1);
        const preview = useSandbox('artifact-' + version, 'registry-a');
        return <main>
          <button onClick={() => preview.syncAndRestart({'app.json':'{}'})}>Start</button>
          <button onClick={() => setVersion(version + 1)}>Next version</button>
          <button onClick={() => window.previewSocket.onmessage({data:JSON.stringify({type:'status',status:'error',lastError:'Container expired'})})}>Expire</button>
          <output aria-label="Version">{version}</output>
          <output aria-label="State">{JSON.stringify({status:preview.sandboxStatus,url:preview.livePreviewUrl,error:preview.sandboxError,syncing:preview.syncing})}</output>
        </main>;
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    ` },
    bundle: true, write: false, jsx: 'automatic', nodePaths: [path.join(shell, 'node_modules')],
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
    if (!req.url.startsWith('/api/')) { res.setHeader('Content-Type', 'text/html'); res.end('<div id="root"></div><script src="/fixture.js"></script>'); return; }
    requests.push(req.url);
    res.setHeader('Content-Type', 'application/json');
    if (req.url.includes('/artifacts/')) res.end(JSON.stringify({sandboxId: 'sandbox-' + new URL(req.url, 'http://local').pathname.split('/')[3]}));
    else if (req.url.endsWith('/start')) {
      const finish = () => res.end(JSON.stringify(failStart ? {status:'error',previewUrl:null,message:'Backend startup failed'} : {status:'running',previewUrl:'http://preview.example'}));
      if (delayNextStart) { delayNextStart = false; delayedStart = finish; } else finish();
    } else if (req.url.endsWith('/status')) res.end(JSON.stringify({status:'running',previewUrl:'http://preview.example'}));
    else res.end('{"ok":true}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const state = async () => JSON.parse(await page.getByLabel('State').textContent());
  await page.getByRole('button', {name:'Start',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.equal(requests[0], '/api/artifacts/artifact-1/sandbox?build_registry_id=registry-a');
  assert.equal((await state()).url, 'http://preview.example');
  await page.getByRole('button', {name:'Expire',exact:true}).click();
  await expect.poll(async () => (await state()).error).toBe('Container expired');
  assert.equal((await state()).url, null);
  failStart = true;
  await page.getByRole('button', {name:'Start',exact:true}).click();
  await expect.poll(async () => (await state()).error).toBe('Backend startup failed');
  assert.equal((await state()).url, null);
  failStart = false;
  delayNextStart = true;
  await page.getByRole('button', {name:'Start',exact:true}).click();
  await expect.poll(() => Boolean(delayedStart)).toBe(true);
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await expect(page.getByLabel('Version')).toHaveText('2');
  delayedStart();
  await expect.poll(async () => (await state()).syncing).toBe(false);
  assert.equal((await state()).url, null);
  assert.equal((await state()).status, null);
  await page.getByRole('button', {name:'Start',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.ok(requests.includes('/api/artifacts/artifact-2/sandbox?build_registry_id=registry-a'));
});
