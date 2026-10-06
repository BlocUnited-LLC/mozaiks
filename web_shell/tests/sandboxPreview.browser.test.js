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
  let delayedCreate = null;
  let delayNextCreate = false;
  let delayedStop = null;
  let delayNextStop = false;
  let previousExpired = false;
  let failStop = false;
  let stopFailureStatus = 500;
  let busyStops = 0;
  let stopRetryAfter = '2';
  let failedSessionRemoved = false;
  let delayedStatus = null;
  let delayNextStatus = false;
  let previewUrl;
  const activeSessions = new Set();
  let maxActiveSessions = 0;
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React, { useState } from 'react';
      import { createRoot } from 'react-dom/client';
      import { useSandbox } from ${JSON.stringify(hook)};
      import PreviewPane from ${JSON.stringify(pane)};
      function Fixture() {
        const [version, setVersion] = useState(1);
        const [registry, setRegistry] = useState('registry-a');
        const [refining, setRefining] = useState(false);
        const preview = useSandbox('artifact-' + version, registry);
        window.tryPreview = () => preview.syncAndRestart({'app.json':'{}'});
        window.tryStop = () => preview.stopPreview();
        return <main>
          <h1 id="workspace-brand" style={{color:'var(--color-primary)',fontFamily:'sans-serif'}}>Mozaiks builder</h1>
          <PreviewPane
            previewUrl={preview.livePreviewUrl}
            artifactVersionId={'artifact-' + version}
            previewArtifactId={preview.previewArtifactId}
            refinementPending={refining}
            sandboxStatus={preview.sandboxStatus}
            sandboxSyncing={preview.syncing}
            sandboxError={preview.sandboxError}
            onStartPreview={() => preview.syncAndRestart({'app.json':'{}'})}
            onStopPreview={preview.sandboxId ? preview.stopPreview : null}
            sandboxStopping={preview.stopping}
            canStartPreview
          />
          <button onClick={() => setVersion(version + 1)}>Next version</button>
          <button onClick={() => setVersion(version - 1)}>Previous version</button>
          <button onClick={() => setRegistry('registry-b')}>Other app</button>
          <button onClick={() => setRefining(!refining)}>Toggle refinement</button>
          <button onClick={() => window.previewSocket.onmessage({data:JSON.stringify({type:'status',status:'error',lastError:'Container expired'})})}>Expire</button>
          <output aria-label="Version">{version}</output>
          <output aria-label="State" style={{display:'block',overflowWrap:'anywhere'}}>{JSON.stringify({status:preview.sandboxStatus,url:preview.livePreviewUrl,version:preview.previewArtifactId,error:preview.sandboxError,syncing:preview.syncing,stopping:preview.stopping})}</output>
        </main>;
      }
      const root = createRoot(document.getElementById('root'));
      window.unmountPreview = () => root.unmount();
      root.render(<Fixture />);
    ` },
    bundle: true, write: false, jsx: 'automatic', loader: {'.js': 'jsx'}, nodePaths: [path.join(shell, 'node_modules')],
    plugins: [{ name: 'preview-transport-fixture', setup(builder) {
      builder.onResolve({filter: /websocketAuth\.js$/}, () => ({path: 'socket', namespace: 'fixture'}));
      builder.onResolve({filter: /studioApi\.js$/}, () => ({path: 'http', namespace: 'fixture'}));
      builder.onLoad({filter: /.*/, namespace: 'fixture'}, ({path: kind}) => ({contents: kind === 'socket'
        ? 'export function openAuthenticatedWebSocket() { const socket = {close(){}}; window.previewSockets ||= []; window.previewSockets.push(socket); window.previewSocket = socket; return socket; }'
        : 'export const getStudioAccessToken = () => null; export const studioFetch = (...args) => fetch(...args);', loader: 'js'}));
    }}],
  });
  const server = http.createServer((req, res) => {
    if (req.url === '/fixture.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(bundle.outputFiles[0].text); return; }
    if (req.url.startsWith('/preview')) {
      res.setHeader('Content-Type', 'text/html');
      res.end('<style>:root{--color-primary:#c2410c}body{font-family:serif;color:var(--color-primary)}</style><h1>Bakery</h1><button onclick="this.textContent=\'Added\'">Add item</button>');
      return;
    }
    if (!req.url.startsWith('/api/')) { res.setHeader('Content-Type', 'text/html'); res.end('<style>:root{--color-primary:#06b6d4}</style><div id="root"></div><script src="/fixture.js"></script>'); return; }
    requests.push(req.url);
    res.setHeader('Content-Type', 'application/json');
    const sandboxId = req.url.split('/')[3];
    if (req.url.includes('/artifacts/')) {
      const finish = () => {
        const sid = 'sandbox-' + new URL(req.url, 'http://local').pathname.split('/')[3]
          + '-' + new URL(req.url, 'http://local').searchParams.get('build_registry_id');
        if (activeSessions.size && !activeSessions.has(sid)) {
          res.statusCode = 409;
          res.end('{"detail":"Preview quota one exceeded"}');
          return;
        }
        activeSessions.add(sid);
        maxActiveSessions = Math.max(maxActiveSessions, activeSessions.size);
        res.end(JSON.stringify({sandboxId:sid}));
      };
      if (delayNextCreate) { delayNextCreate = false; delayedCreate = finish; } else finish();
    }
    else if (req.url.endsWith('/start')) {
      const finish = () => res.end(JSON.stringify(failStart ? {status:'error',previewUrl:null,message:'Backend startup failed'} : {status:'running',previewUrl:previewUrl + '?session=' + sandboxId}));
      if (delayNextStart) { delayNextStart = false; delayedStart = finish; } else finish();
    } else if (req.url.endsWith('/stop') && delayNextStop) {
      delayNextStop = false;
      delayedStop = () => { activeSessions.delete(sandboxId); res.end('{"ok":true}'); };
    } else if (req.url.endsWith('/stop') && busyStops > 0) {
      busyStops -= 1;
      res.statusCode = 409;
      if (stopRetryAfter !== null) res.setHeader('Retry-After', stopRetryAfter);
      res.end('{"detail":"Preview operation is already in progress"}');
    } else if (req.url.endsWith('/stop') && failStop) {
      res.statusCode = stopFailureStatus;
      res.end('{"detail":"Provider could not confirm termination"}');
    } else if (req.url.endsWith('/stop') && previousExpired) {
      activeSessions.delete(sandboxId);
      res.statusCode = 404;
      res.end('{"detail":"Sandbox not found"}');
    } else if (req.url.endsWith('/stop')) {
      activeSessions.delete(sandboxId);
      res.end('{"ok":true}');
    } else if (req.url.endsWith('/status')) {
      const finish = () => {
        if (failedSessionRemoved) { res.statusCode = 404; res.end('{"detail":"Sandbox not found"}'); }
        else res.end(JSON.stringify({status:'running',previewUrl:previewUrl + '?session=' + sandboxId}));
      };
      if (delayNextStatus) { delayNextStatus = false; delayedStatus = finish; } else finish();
    }
    else res.end('{"ok":true}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  previewUrl = `http://127.0.0.1:${server.address().port}/preview`;
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.clock.install();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const state = async () => JSON.parse(await page.getByLabel('State').textContent());
  const runningUrl = (version, registry = 'registry-a') => previewUrl + '?session=sandbox-artifact-' + version + '-' + registry;
  await expect(page.getByText('Draft app preview', {exact:true})).toBeVisible();
  await expect(page.getByText('Temporary preview · Changes here do not publish your app.')).toBeVisible();
  await expect(page.getByText('Version artifact-1')).toBeVisible();
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.equal(requests[0], '/api/artifacts/artifact-1/sandbox?build_registry_id=registry-a');
  assert.equal((await state()).url, runningUrl(1));
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
  assert.equal(popup.url(), runningUrl(1));
  assert.equal(await popup.evaluate(() => window.opener), null);
  await expect(page.getByRole('heading', {name:'Mozaiks builder'})).toBeVisible();
  await popup.close();
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button', {name:'Toggle refinement',exact:true}).click();
  await expect(page.getByText('Making your changes. You can keep trying this preview.')).toBeVisible();
  await expect(frame.getByRole('button', {name:'Added'})).toBeVisible();
  await page.getByRole('button', {name:'Toggle refinement',exact:true}).click();
  const beforeCandidate = requests.length;
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await expect(page.getByLabel('Version')).toHaveText('2');
  await expect(page.getByText('Preview based on version artifact-1')).toBeVisible();
  await expect(page.getByText('A different draft is selected.')).toBeVisible();
  await expect(page.getByRole('button', {name:'Update preview',exact:true})).toBeVisible();
  await expect(frame.getByRole('button', {name:'Added'})).toBeVisible();
  assert.equal((await state()).url, runningUrl(1));
  assert.equal(requests.length, beforeCandidate, 'Selecting a new candidate leaves the running iframe and lifecycle untouched');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const oldSocket = await page.evaluateHandle(() => window.previewSocket);
  delayNextStop = true;
  await page.getByRole('button', {name:'Update preview',exact:true}).click();
  await expect.poll(() => Boolean(delayedStop)).toBe(true);
  await page.evaluate(() => window.tryPreview());
  assert.deepEqual(requests.slice(beforeCandidate), ['/api/sandbox/sandbox-artifact-1-registry-a/stop']);
  delayedStop();
  delayedStop = null;
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.deepEqual(requests.slice(beforeCandidate), [
    '/api/sandbox/sandbox-artifact-1-registry-a/stop',
    '/api/artifacts/artifact-2/sandbox?build_registry_id=registry-a',
    '/api/sandbox/sandbox-artifact-2-registry-a/sync',
    '/api/sandbox/sandbox-artifact-2-registry-a/start',
  ]);
  await expect(page.getByText('Preview based on version artifact-2')).toBeVisible();
  await expect(frame.getByRole('button', {name:'Add item',exact:true})).toBeVisible();
  await oldSocket.evaluate(socket => socket.onmessage({data:JSON.stringify({type:'status',status:'error',lastError:'Obsolete session expired'})}));
  assert.equal((await state()).url, runningUrl(2), 'Old session events cannot alter the replacement');
  await page.setViewportSize({width:1280,height:900});
  // Expiry must still be observed for retained V2 after candidate V3 arrives.
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await expect(page.getByText('Preview based on version artifact-2')).toBeVisible();
  delayNextStatus = true;
  await page.clock.runFor(10001);
  await expect.poll(() => Boolean(delayedStatus)).toBe(true);
  await page.getByRole('button', {name:'Expire',exact:true}).click();
  await expect.poll(async () => (await state()).error).toBe('Container expired');
  assert.equal((await state()).url, null);
  await expect(page.locator('iframe')).toHaveCount(0);
  await expect(page.getByRole('link', {name:'Open draft preview',exact:true})).toHaveCount(0);
  failStart = true;
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).error).toBe('Backend startup failed');
  assert.equal((await state()).url, null);
  failedSessionRemoved = true;
  delayedStatus();
  const failedPolls = requests.filter(url => url.endsWith('/status')).length;
  await page.clock.runFor(20001);
  await expect.poll(async () => (await state()).error).toBe('Backend startup failed');
  assert.equal(requests.filter(url => url.endsWith('/status')).length, failedPolls,
    'A failed preview stops polling; sandbox cleanup must not replace the startup cause');
  await expect(page.getByText('Preview needs attention', {exact:true})).toBeVisible();
  await expect(page.getByText('Backend startup failed', {exact:true})).toBeHidden();
  await page.getByText('Preview details', {exact:true}).click();
  await expect(page.getByText('Backend startup failed', {exact:true})).toBeVisible();
  failedSessionRemoved = false;
  failStart = false;
  delayNextStart = true;
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(() => Boolean(delayedStart)).toBe(true);
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await expect(page.getByLabel('Version')).toHaveText('4');
  await expect(page.getByText('Version artifact-4')).toBeVisible();
  delayedStart();
  await expect.poll(async () => (await state()).syncing).toBe(false);
  assert.equal((await state()).url, null);
  assert.equal((await state()).status, null);
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.ok(requests.includes('/api/artifacts/artifact-4/sandbox?build_registry_id=registry-a'));
  assert.ok(requests.includes('/api/sandbox/sandbox-artifact-3-registry-a/stop'), 'An abandoned start is cleaned before another allocation');
  previousExpired = true;
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await expect(page.getByLabel('Version')).toHaveText('5');
  await page.getByRole('button', {name:'Update preview',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.ok(requests.includes('/api/artifacts/artifact-5/sandbox?build_registry_id=registry-a'), 'An expired previous preview does not block another saved version');
  previousExpired = false;
  await page.getByRole('button', {name:'Stop preview',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe(null);
  await expect(page.locator('iframe')).toHaveCount(0);
  await expect(page.getByRole('link', {name:'Open draft preview',exact:true})).toHaveCount(0);
  assert.ok(requests.includes('/api/sandbox/sandbox-artifact-5-registry-a/stop'));
  await expect(page.getByRole('button', {name:'Start draft preview',exact:true})).toBeVisible();

  // A pending allocation remains the only request across version changes. Its
  // late session must be stopped before the next version can allocate a session.
  const allocationRequests = requests.length;
  delayNextCreate = true;
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(() => Boolean(delayedCreate)).toBe(true);
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await expect(page.getByLabel('Version')).toHaveText('6');
  await expect.poll(async () => (await state()).syncing).toBe(true);
  await expect(page.getByRole('button', {name:'Start draft preview',exact:true})).toHaveCount(0);
  await page.evaluate(() => window.tryPreview());
  assert.deepEqual(requests.slice(allocationRequests), ['/api/artifacts/artifact-5/sandbox?build_registry_id=registry-a']);
  delayedCreate();
  await expect.poll(async () => (await state()).syncing).toBe(false);
  assert.equal((await state()).url, null);
  assert.equal((await state()).status, null);
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.deepEqual(requests.slice(allocationRequests, allocationRequests + 3), [
    '/api/artifacts/artifact-5/sandbox?build_registry_id=registry-a',
    '/api/sandbox/sandbox-artifact-5-registry-a/stop',
    '/api/artifacts/artifact-6/sandbox?build_registry_id=registry-a',
  ]);
  const nextRequests = requests.length;
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await page.getByRole('button', {name:'Update preview',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.deepEqual(requests.slice(nextRequests, nextRequests + 2), [
    '/api/sandbox/sandbox-artifact-6-registry-a/stop',
    '/api/artifacts/artifact-7/sandbox?build_registry_id=registry-a',
  ]);

  // Stopping an old version also retains admission until its response settles.
  delayNextStop = true;
  await page.getByRole('button', {name:'Stop preview',exact:true}).click();
  await expect.poll(() => Boolean(delayedStop)).toBe(true);
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await expect.poll(async () => (await state()).syncing).toBe(true);
  await expect(page.getByRole('button', {name:'Start draft preview',exact:true})).toHaveCount(0);
  delayedStop();
  await expect.poll(async () => (await state()).syncing).toBe(false);
  assert.equal((await state()).stopping, false);
  assert.equal((await state()).status, null);
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).status).toBe('running');
  assert.ok(requests.includes('/api/artifacts/artifact-8/sandbox?build_registry_id=registry-a'));

  // A failed termination must keep its cleanup handle and refuse a second allocation.
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  const beforeFailedStop = requests.length;
  failStop = true;
  await page.getByRole('button', {name:'Update preview',exact:true}).click();
  await expect.poll(async () => (await state()).error).toBe('Provider could not confirm termination');
  assert.equal((await state()).url, null);
  assert.deepEqual(requests.slice(beforeFailedStop), ['/api/sandbox/sandbox-artifact-8-registry-a/stop']);
  await page.getByRole('button', {name:'Previous version',exact:true}).click();
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).error).toBe('Provider could not confirm termination');
  assert.deepEqual(requests.slice(beforeFailedStop), [
    '/api/sandbox/sandbox-artifact-8-registry-a/stop',
    '/api/sandbox/sandbox-artifact-8-registry-a/stop',
  ], 'Selecting the old artifact still retries its uncertain termination before allocation');
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  failStop = false;
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).version).toBe('artifact-9');
  assert.deepEqual(requests.slice(beforeFailedStop, beforeFailedStop + 4), [
    '/api/sandbox/sandbox-artifact-8-registry-a/stop',
    '/api/sandbox/sandbox-artifact-8-registry-a/stop',
    '/api/sandbox/sandbox-artifact-8-registry-a/stop',
    '/api/artifacts/artifact-9/sandbox?build_registry_id=registry-a',
  ]);

  // Another candidate during replacement cancels adoption, not quota admission.
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  delayNextStop = true;
  const beforeAbandonedUpdate = requests.length;
  delayedStop = null;
  await page.getByRole('button', {name:'Update preview',exact:true}).click();
  await expect.poll(() => Boolean(delayedStop)).toBe(true);
  await page.getByRole('button', {name:'Next version',exact:true}).click();
  await page.evaluate(() => window.tryPreview());
  delayedStop();
  await expect.poll(async () => (await state()).syncing).toBe(false);
  assert.deepEqual(requests.slice(beforeAbandonedUpdate), ['/api/sandbox/sandbox-artifact-9-registry-a/stop']);
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).version).toBe('artifact-11');

  const beforeRegistryChange = requests.length;
  await page.getByRole('button', {name:'Other app',exact:true}).click();
  await expect(page.locator('iframe')).toHaveCount(0);
  await expect(page.getByRole('link', {name:'Open draft preview',exact:true})).toHaveCount(0);
  assert.equal((await state()).version, null);
  assert.equal(requests.length, beforeRegistryChange, 'Switching app hides its preview without allocating another');
  await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
  await expect.poll(async () => (await state()).url).toBe(runningUrl(11, 'registry-b'));
  assert.deepEqual(requests.slice(beforeRegistryChange, beforeRegistryChange + 2), [
    '/api/sandbox/sandbox-artifact-11-registry-a/stop',
    '/api/artifacts/artifact-11/sandbox?build_registry_id=registry-b',
  ]);
  assert.equal(maxActiveSessions, 1, 'Replacement never exceeds one allocated preview');

  await t.test('busy stops retry within bounds without duplicate requests or premature allocation', async () => {
    await page.clock.pauseAt(await page.evaluate(() => new Date(Date.now() + 1000).toISOString()));
    const stop11 = '/api/sandbox/sandbox-artifact-11-registry-b/stop';
    const stop12 = '/api/sandbox/sandbox-artifact-12-registry-b/stop';
    const busyResponse = () => page.waitForResponse(response => response.url().endsWith('/stop') && response.status() === 409);
    const firstBusy = busyResponse();
    busyStops = 1;
    const beforeExplicitStop = requests.length;
    const retainedSocket = await page.evaluateHandle(() => window.previewSocket);
    await page.getByRole('button', {name:'Stop preview',exact:true}).click();
    await (await firstBusy).finished();
    await expect.poll(async () => (await state()).stopping).toBe(true);
    await expect(page.getByRole('button', {name:'Stopping preview…',exact:true})).toBeDisabled();
    await retainedSocket.evaluate(socket => socket.onmessage({data:JSON.stringify({type:'status',status:'running',previewUrl:'/stale-preview'})}));
    await page.evaluate(() => { window.tryPreview(); window.tryStop(); });
    await page.clock.runFor(1999);
    assert.deepEqual(requests.slice(beforeExplicitStop), [stop11]);
    assert.equal((await state()).url, null, 'Status events cannot revive a preview while stop waits to retry');
    await page.clock.runFor(1);
    await expect.poll(async () => (await state()).status).toBe(null);
    assert.deepEqual(requests.slice(beforeExplicitStop), [stop11, stop11]);
    await expect(page.getByRole('button', {name:'Start draft preview',exact:true})).toBeVisible();
    await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
    await expect.poll(async () => (await state()).status).toBe('running');

    // A replacement has the same retry semantics and keeps the quota occupied
    // until the old preview's stop has actually succeeded.
    await page.getByRole('button', {name:'Next version',exact:true}).click();
    busyStops = 1;
    const replacementBusy = busyResponse();
    const beforeReplacement = requests.length;
    await page.getByRole('button', {name:'Update preview',exact:true}).click();
    await (await replacementBusy).finished();
    await expect.poll(async () => (await state()).syncing).toBe(true);
    await page.evaluate(() => window.tryPreview());
    await page.clock.runFor(1999);
    assert.deepEqual(requests.slice(beforeReplacement), [stop11]);
    assert.deepEqual([...activeSessions], ['sandbox-artifact-11-registry-b']);
    await page.clock.runFor(1);
    await expect.poll(async () => (await state()).version).toBe('artifact-12');
    assert.deepEqual(requests.slice(beforeReplacement), [
      stop11, stop11,
      '/api/artifacts/artifact-12/sandbox?build_registry_id=registry-b',
      '/api/sandbox/sandbox-artifact-12-registry-b/sync',
      '/api/sandbox/sandbox-artifact-12-registry-b/start',
    ]);

    // Persistent contention is bounded, including an excessive Retry-After.
    busyStops = 10;
    stopRetryAfter = '600';
    const beforeExhaustion = requests.length;
    let busy = busyResponse();
    await page.getByRole('button', {name:'Stop preview',exact:true}).click();
    await (await busy).finished();
    busy = busyResponse();
    await page.clock.runFor(5000);
    await (await busy).finished();
    busy = busyResponse();
    await page.clock.runFor(5000);
    await (await busy).finished();
    await expect.poll(async () => (await state()).error).toBe('Preview operation is already in progress');
    await expect(page.getByText('Preview needs attention', {exact:true})).toBeVisible();
    await expect(page.getByRole('button', {name:'Stop preview',exact:true})).toBeEnabled();
    await page.clock.runFor(10000);
    assert.deepEqual(requests.slice(beforeExhaustion), [stop12, stop12, stop12]);
    assert.deepEqual([...activeSessions], ['sandbox-artifact-12-registry-b']);
    busyStops = 0;
    stopRetryAfter = '2';
    await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
    await expect.poll(async () => (await state()).status).toBe('running');
    assert.deepEqual(requests.slice(beforeExhaustion + 3, beforeExhaustion + 5), [
      stop12, '/api/artifacts/artifact-12/sandbox?build_registry_id=registry-b',
    ], 'Retrying the same artifact first confirms the retained cleanup handle');

    failStop = true;
    stopFailureStatus = 403;
    const beforeForbidden = requests.length;
    await page.getByRole('button', {name:'Stop preview',exact:true}).click();
    await expect.poll(async () => (await state()).error).toBe('Provider could not confirm termination');
    await page.clock.runFor(10000);
    assert.deepEqual(requests.slice(beforeForbidden), [stop12], 'Forbidden stops are never retried');
    failStop = false;
    await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
    await expect.poll(async () => (await state()).status).toBe('running');

    // A new selection cancels future retry requests but retains the handle for
    // cleanup when the user explicitly starts the newly selected candidate.
    await page.getByRole('button', {name:'Next version',exact:true}).click();
    busyStops = 1;
    stopRetryAfter = null;
    const beforeCancelledRetry = requests.length;
    busy = busyResponse();
    await page.getByRole('button', {name:'Update preview',exact:true}).click();
    await (await busy).finished();
    await page.getByRole('button', {name:'Next version',exact:true}).click();
    await page.evaluate(() => window.tryPreview());
    await page.clock.runFor(2000);
    await expect.poll(async () => (await state()).syncing).toBe(false);
    assert.deepEqual(requests.slice(beforeCancelledRetry), [stop12]);
    assert.equal((await state()).url, null);
    await page.getByRole('button', {name:'Start draft preview',exact:true}).click();
    await expect.poll(async () => (await state()).version).toBe('artifact-14');
    assert.deepEqual(requests.slice(beforeCancelledRetry, beforeCancelledRetry + 3), [
      stop12, stop12, '/api/artifacts/artifact-14/sandbox?build_registry_id=registry-b',
    ]);

    busyStops = 1;
    const beforeUnmount = requests.length;
    busy = busyResponse();
    await page.getByRole('button', {name:'Stop preview',exact:true}).click();
    await (await busy).finished();
    await page.evaluate(() => window.unmountPreview());
    await page.clock.runFor(10000);
    assert.deepEqual(requests.slice(beforeUnmount), ['/api/sandbox/sandbox-artifact-14-registry-b/stop']);
    assert.equal(activeSessions.size, 1, 'Cancelling a busy stop must not claim provider cleanup completed');
    assert.equal(maxActiveSessions, 1);
  });
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
