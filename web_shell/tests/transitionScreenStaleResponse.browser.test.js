/**
 * TransitionScreen renders only the response for its current transition.
 *
 * A live Create App journey (2026-10-12) finished ThemeCapture, and the chat
 * page set its pending transition to `workflow_complete` and then, within
 * milliseconds, to the journey's `coding_journey_selector`. The screen fetched
 * both definitions; the earlier `workflow_complete` response arrived last and
 * replaced the selector with a generic completion card. Its Continue button
 * resolved the *current* id without an option, the backend answered
 * "option_id is required for transition 'coding_journey_selector'", and the
 * user had no way to choose. These tests replay that arrival order against the
 * real component.
 */
import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test, { after, before } from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.resolve(shell, '../chat-ui/src');
let browser;
let server;
let origin;

before(async () => {
  const stubs = {
    componentRegistry: `import React from 'react';
      export const hasComponent = () => false;
      export const getComponent = () => null;`,
    LauncherScreen: `import React from 'react';
      export const LauncherScreen = ({transition}) => <div data-testid="screen" data-transition={transition.id}>{transition.id}</div>;`,
    ConfirmScreen: `import React from 'react';
      export const ConfirmScreen = ({transition}) => <div data-testid="screen" data-transition={transition.id}>{transition.id}</div>;`,
    TransitionOverlayFrame: `import React from 'react';
      export const TransitionOverlayFrame = ({children}) => <>{children}</>;`,
    useAppEventBus: 'export const useAppEventBus = () => {};',
  };
  const bundle = await build({
    stdin: { contents: `
      import React,{useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import {BrowserRouter} from 'react-router-dom';
      import {TransitionScreen} from ${JSON.stringify(path.join(ui, 'ui/screens/TransitionScreen.jsx'))};
      function Fixture(){
        const [id,setId]=useState(null);
        window.setTransition=setId;
        return id ? <TransitionScreen transitionId={id} onNavigate={()=>{}} context={{}}/> : null;
      }
      createRoot(document.getElementById('root')).render(<BrowserRouter><Fixture/></BrowserRouter>);
    `, loader: 'jsx', resolveDir: shell },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx' },
    alias: Object.fromEntries(['react', 'react-dom', 'react-router-dom'].map(name => [name, path.join(shell, 'node_modules', name)])),
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'screen-boundaries', setup(builder) {
      builder.onResolve({ filter: /(?:componentRegistry|LauncherScreen|ConfirmScreen|TransitionOverlayFrame|useAppEventBus)(?:\.[jt]sx?)?$/ }, args => {
        const name = path.basename(args.path).replace(/\.[jt]sx?$/, '');
        return { path: name, namespace: 'screen-boundary' };
      });
      builder.onLoad({ filter: /.*/, namespace: 'screen-boundary' }, args => ({ contents: stubs[args.path], loader: 'jsx', resolveDir: shell }));
    } }],
  });
  server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text
      : '<!doctype html><div id="root"></div><script type="module" src="/fixture.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true });
});

after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => { server.closeAllConnections(); server.close(resolve); });
});

const definitions = {
  workflow_complete: { id: 'workflow_complete', transition_type: 'confirm' },
  coding_journey_selector: { id: 'coding_journey_selector', transition_type: 'user_choice', options: [{ id: 'autonomous' }, { id: 'guided' }] },
};

async function openWithDelays(delays) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/api/transitions/*', async route => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop());
    await new Promise(resolve => setTimeout(resolve, delays[id] ?? 0));
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(definitions[id]) }).catch(() => {});
  });
  await page.goto(origin + '/');
  await page.waitForFunction(() => typeof window.setTransition === 'function');
  return { page, errors };
}

test('a slower response for a superseded transition never replaces the current one', async t => {
  const { page, errors } = await openWithDelays({ workflow_complete: 600, coding_journey_selector: 50 });
  t.after(() => page.close());
  await page.evaluate(() => window.setTransition('workflow_complete'));
  await page.waitForTimeout(20);
  await page.evaluate(() => window.setTransition('coding_journey_selector'));

  await page.waitForSelector('[data-testid="screen"]');
  await page.waitForTimeout(900); // the superseded response has arrived by now
  assert.equal(await page.getAttribute('[data-testid="screen"]', 'data-transition'), 'coding_journey_selector');
  assert.deepEqual(errors, []);
});

test('the current transition still renders when it is the slower response', async t => {
  const { page, errors } = await openWithDelays({ workflow_complete: 50, coding_journey_selector: 400 });
  t.after(() => page.close());
  await page.evaluate(() => window.setTransition('workflow_complete'));
  await page.waitForTimeout(10);
  await page.evaluate(() => window.setTransition('coding_journey_selector'));

  await page.waitForTimeout(700);
  assert.equal(await page.getAttribute('[data-testid="screen"]', 'data-transition'), 'coding_journey_selector');
  assert.deepEqual(errors, []);
});
