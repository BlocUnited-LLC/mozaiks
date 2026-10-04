import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);
const chatUi = path.join(root, 'chat-ui/src');
const transitionsRoot = path.join(root, 'factory_app/workflows/extended_orchestration');

async function fixture(t) {
  const registry = JSON.parse(await fs.readFile(path.join(transitionsRoot, 'extension_registry.json'), 'utf8'));
  const entry = registry.entrypoints.find(item => item.path === '/create');
  const transitions = Object.fromEntries(registry.transitions.map(item => [item.id, item]));
  const bundle = await build({
    stdin: { contents: `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { BrowserRouter } from 'react-router-dom';
      import RouteRenderer from ${JSON.stringify(path.join(chatUi, 'components/RouteRenderer.jsx'))};
      import { registerComponent } from ${JSON.stringify(path.join(chatUi, 'registry/componentRegistry.js'))};
      import AppTypeSelector from ${JSON.stringify(path.join(transitionsRoot, 'ui/transitions/AppTypeSelector.js'))};
      import BrownfieldPathSelector from ${JSON.stringify(path.join(transitionsRoot, 'ui/transitions/BrownfieldPathSelector.js'))};
      import BrownfieldRepoInput from ${JSON.stringify(path.join(transitionsRoot, 'ui/transitions/BrownfieldRepoInput.js'))};
      window.mozaiksAuth = { getAccessToken: () => 'fixture-token' };
      registerComponent('AppTypeSelector', AppTypeSelector);
      registerComponent('BrownfieldPathSelector', BrownfieldPathSelector);
      registerComponent('BrownfieldRepoInput', BrownfieldRepoInput);
      registerComponent('ChatPage', () => <h1>Workflow opened</h1>);
      createRoot(document.getElementById('root')).render(<BrowserRouter><RouteRenderer isAuthenticated /></BrowserRouter>);
    `, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
    loader: { '.js': 'jsx', '.jpg': 'dataurl', '.png': 'dataurl', '.svg': 'dataurl' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: {
      react: path.join(shell, 'node_modules/react'),
      'react-dom': path.join(shell, 'node_modules/react-dom'),
      'react-router-dom': path.join(shell, 'node_modules/react-router-dom'),
      '@mozaiks/chat-ui/platform': path.join(chatUi, 'platform/index.js'),
    },
    define: { 'process.env': '{}', 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'host-context', setup(builder) {
      builder.onResolve({ filter: /(?:NavigationProvider|ChatUIContext|layout\/(?:Header|Footer|MobileBottomBar)|styles\/useTheme|styles\/brandAssets)$/ }, args => ({ path: args.path, namespace: 'host-context' }));
      builder.onLoad({ filter: /.*/, namespace: 'host-context' }, args => ({ contents:
        args.path.endsWith('NavigationProvider')
          ? `const entry=${JSON.stringify({ ...entry, meta: { appShell: false } })};entry.transition=window.fixtureEntryTransition||entry.transition;const navigation={pages:[entry],loading:false,navigation:{}};export const useNavigation=()=>navigation;`
          : args.path.endsWith('ChatUIContext')
            ? `const context={user:{id:'owner',app_id:'studio-host'},config:{appId:'studio-host'},auth:{getAccessToken:()=> 'fixture-token'},loading:false};export const useChatUI=()=>context;`
            : args.path.endsWith('useTheme') ? 'export const useTheme=()=>({});'
              : args.path.endsWith('brandAssets') ? 'export const getChatBackgroundSrc=()=>null;'
                : 'export default function Chrome(){return null;}',
      }));
    } }],
  });
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell, 'styles.css'), 'utf8'))
      + `\n@source "${chatUi.replaceAll('\\', '/')}";`
      + `\n@source "${transitionsRoot.replaceAll('\\', '/')}";`,
    { from: path.join(shell, 'styles.css') },
  );
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>${styles.css}</style></head><body style="margin:0"><div id="root"></div><script type="module" src="/fixture.js"></script></body></html>`;
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const baseUrl = `http://127.0.0.1:${server.address().port}`;

  return async (subtest, { createFailure = false, resolveFailure = false, holdCreate = false, holdResolve = false, entryTransition = null } = {}) => {
    const page = await browser.newPage({ reducedMotion: 'reduce' });
    subtest.after(() => page.close());
    const calls = { creates: [], resolves: [], errors: [], unexpected: [] };
    let releaseCreate;
    let releaseResolve;
    const creationGate = holdCreate ? new Promise(resolve => { releaseCreate = resolve; }) : Promise.resolve();
    const resolutionGate = holdResolve ? new Promise(resolve => { releaseResolve = resolve; }) : Promise.resolve();
    await page.addInitScript(value => { window.fixtureEntryTransition = value; }, entryTransition);
    page.on('pageerror', error => calls.errors.push(error.message));
    subtest.after(() => {
      assert.deepEqual(calls.errors, []);
      assert.deepEqual(calls.unexpected, []);
    });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const json = (body, status = 200) => route.fulfill({ status, json: body });
      if (url.origin === baseUrl && ['/create', '/fixture.js'].includes(url.pathname)) return route.continue();
      if (url.origin !== baseUrl) { calls.unexpected.push(request.url()); return route.abort(); }
      if (url.pathname === '/api/studio/apps' && request.method() === 'POST') {
        calls.creates.push({ body: request.postDataJSON(), headers: request.headers() });
        await creationGate;
        if (createFailure && calls.creates.length === 1) return json({ detail: 'Workspace creation unavailable' }, 503);
        return json({ app: { build_registry_id: 'registered-app', app_id: 'generated-app' } });
      }
      if (url.pathname === '/api/transitions/resolve' && request.method() === 'POST') {
        const body = request.postDataJSON();
        calls.resolves.push({ body, headers: request.headers() });
        await resolutionGate;
        if (resolveFailure && calls.resolves.length === 1) return json({ detail: 'Try the build again' }, 503);
        const transition = transitions[body.transition_id];
        const option = body.option_id == null ? transition : transition.options.find(item => item.id === body.option_id);
        assert.ok(option, 'the UI must resolve an option from the real transition registry');
        const context = { ...body.context_variables, ...option.context_variables };
        if (transitions[option.route_to]) return json({
          resolution_type: 'transition', transition: transitions[option.route_to],
          context_variables: context, journey_id: option.sequence || body.journey_id,
        });
        return json({
          resolution_type: transition.transition_type === 'chat_session' ? 'chat_session' : 'workflow',
          workflow_id: option.route_to, chat_id: 'new-chat',
        });
      }
      if (url.pathname.startsWith('/api/transitions/')) {
        const transition = transitions[decodeURIComponent(url.pathname.split('/').at(-1))];
        assert.ok(transition);
        return json(transition);
      }
      if (url.pathname === '/api/oauth/github/status') return json({ configured: false });
      calls.unexpected.push(request.url());
      return route.abort();
    });
    await page.goto(`${baseUrl}/create`);
    if (!entryTransition) await expect(page.getByRole('heading', { name: 'Choose Your App Journey' })).toBeVisible();
    return { page, calls, releaseCreate, releaseResolve };
  };
}

const greenfield = page => page.getByRole('button', { name: /Build Something New/ });
const brownfield = page => page.getByRole('button', { name: /Existing App/ });

test('registered app journeys use the normal factory selectors and shell transition router', async t => {
  const open = await fixture(t);

  await t.test('fresh greenfield registers once before resolving and forwards scoped identity with auth', async subtest => {
    const { page, calls, releaseCreate } = await open(subtest, { holdCreate: true });
    await page.getByRole('switch', { name: 'Build with monetization' }).click();
    await greenfield(page).evaluate(button => { button.click(); button.click(); });
    await expect(page.getByRole('status')).toHaveText('Opening your app workspace…');
    await expect(greenfield(page)).toBeDisabled();
    await expect(brownfield(page)).toBeDisabled();
    await expect.poll(() => calls.creates.length).toBe(1);
    assert.equal(calls.resolves.length, 0);
    releaseCreate();
    await expect(page.getByRole('heading', { name: 'Workflow opened' })).toBeVisible();
    assert.equal(new URL(page.url()).searchParams.get('workflow'), 'ValueEngine');
    assert.equal(calls.creates.length, 1);
    assert.deepEqual(calls.creates[0].body, {});
    assert.equal(calls.creates[0].headers.authorization, 'Bearer fixture-token');
    assert.equal(calls.resolves.length, 1);
    const { body, headers } = calls.resolves[0];
    assert.equal(headers.authorization, 'Bearer fixture-token');
    assert.equal(body.build_registry_id, 'registered-app');
    assert.equal(body.app_id, 'studio-host');
    assert.equal(body.user_id, 'owner');
    assert.equal(body.journey_id, 'build');
    assert.equal(body.context_variables.monetization_enabled, true);
    assert.equal(Object.hasOwn(body.context_variables, 'build_registry_id'), false);
  });

  await t.test('brownfield keeps one registered target through path selection and repository intake', async subtest => {
    const { page, calls } = await open(subtest);
    await brownfield(page).click();
    await page.getByRole('button', { name: /Add AI Workflows/ }).click();
    await page.getByPlaceholder('acme-corp/my-app').fill('example/customer-app');
    await page.getByRole('button', { name: 'Scan & Start Discovery', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Workflow opened' })).toBeVisible();
    assert.equal(new URL(page.url()).searchParams.get('workflow'), 'ExistingAppDiscovery');
    assert.equal(calls.creates.length, 1);
    assert.deepEqual(calls.resolves.map(({ body }) => body.build_registry_id), Array(3).fill('registered-app'));
    const final = calls.resolves.at(-1).body;
    assert.equal(final.journey_id, 'brownfield_app_adoption');
    assert.equal(final.context_variables.app_type, 'brownfield_app');
    assert.equal(final.context_variables.brownfield_build_path, 'light_integration');
    assert.equal(final.context_variables.github_repo, 'example/customer-app');
    assert.equal(Object.hasOwn(final.context_variables, 'build_registry_id'), false);
  });

  await t.test('registration error is visible and cannot start a workflow; retry recovers', async subtest => {
    const { page, calls } = await open(subtest, { createFailure: true });
    await greenfield(page).click();
    await expect(page.getByRole('alert')).toHaveText('Workspace creation unavailable');
    assert.equal(calls.resolves.length, 0);
    await expect(greenfield(page)).toBeEnabled();
    await greenfield(page).click();
    await expect(page.getByRole('heading', { name: 'Workflow opened' })).toBeVisible();
    assert.equal(calls.creates.length, 2);
    assert.equal(calls.resolves.length, 1);
  });

  for (const disableMonetization of [false, true]) {
    await t.test(`resolution retry keeps one target and ${disableMonetization ? 'clears' : 'retains'} monetization`, async subtest => {
      const { page, calls } = await open(subtest, { resolveFailure: true });
      const monetization = page.getByRole('switch', { name: 'Build with monetization' });
      await monetization.click();
      await greenfield(page).click();
      await expect(page.getByText('Try the build again', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Retry', exact: true }).click();
      await expect(monetization).toBeChecked();
      if (disableMonetization) await monetization.click();
      await greenfield(page).click();
      await expect(page.getByRole('heading', { name: 'Workflow opened' })).toBeVisible();
      assert.equal(calls.creates.length, 1);
      assert.deepEqual(calls.resolves.map(({ body }) => body.build_registry_id), ['registered-app', 'registered-app']);
      const first = calls.resolves[0].body.context_variables;
      const retry = calls.resolves[1].body.context_variables;
      assert.equal(first.monetization_enabled, true);
      assert.equal(retry.monetization_enabled, !disableMonetization);
      assert.deepEqual(retry.builder_options, disableMonetization ? {} : first.builder_options);
    });
  }

  await t.test('automatic transitions resolve once while the backend response is pending', async subtest => {
    const { page, calls, releaseResolve } = await open(subtest, { entryTransition: 'app_review', holdResolve: true });
    await expect.poll(() => calls.resolves.length).toBe(1);
    // Let callback-dependent effects and their zero-delay timers run while the first request stays pending.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(calls.resolves.length, 1);
    assert.equal(calls.creates.length, 0);
    releaseResolve();
    await expect(page.getByRole('heading', { name: 'Workflow opened' })).toBeVisible();
    assert.equal(new URL(page.url()).searchParams.get('workflow'), 'AppReview');
    assert.equal(calls.resolves.length, 1);
  });
});
