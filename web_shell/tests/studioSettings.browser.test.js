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
const pages = path.join(root, 'factory_app/app/admin/pages');
const selection = { kind: 'subscription', dashboard_app_id: 'first-dashboard', build_registry_id: 'first-registry',
  build_id: 'selected-build', app_bundle_id: 'selected-bundle', artifact_version_id: 'selected-contract' };
const descriptor = { selection, app_id: 'studio-host', artifact_kind: 'subscription_contract', artifact_key: 'customer-plans' };
const billing = { status: 'configured', dashboard_app_id: 'first-dashboard', app_name: 'First app', artifact_version_id: 'selected-bundle',
  settings_refinement: descriptor, products: [{ label: 'Customer plans', default_plan_id: 'starter', plans: [{ plan_id: 'starter', label: 'Starter', price: { amount_cents: 0, currency: 'USD', interval: 'month' } }] }] };

test('app settings reuse exact-build refinement, cancellation, saved resume and staged review', async t => {
  const bundle = await build({
    stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';import {BrowserRouter,Routes,Route} from 'react-router-dom';
      import Billing from ${JSON.stringify(path.join(pages, 'AppBillingPage.jsx'))};
      import Branding from ${JSON.stringify(path.join(pages, 'AppBrandingPage.jsx'))};
      window.mozaiksAuth={getAccessToken:()=> 'fixture-token'};
      createRoot(document.getElementById('root')).render(<BrowserRouter><Routes><Route path="/apps/:appId/billing" element={<Billing/>}/><Route path="/apps/:appId/branding" element={<Branding/>}/><Route path="/chat" element={<h1>Saved workflow session</h1>}/></Routes></BrowserRouter>);`, resolveDir: shell, loader: 'jsx' },
    define: { 'process.env': '{}', 'process.env.NODE_ENV': '"test"', 'import.meta.env': '{}' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' },
    nodePaths: [path.join(shell, 'node_modules')], alias: {
      react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom'),
      'react-router-dom': path.join(shell, 'node_modules/react-router-dom'), 'react-icons': path.join(shell, 'node_modules/react-icons'),
      '@mozaiks/chat-ui/ui': path.join(root, 'chat-ui/src/ui/index.js'),
      '@mozaiks/chat-ui/hooks/useWorkflowStart.js': path.join(root, 'chat-ui/src/hooks/useWorkflowStart.js'),
    },
    plugins: [{ name: 'host-context', setup(builder) {
      builder.onResolve({ filter: /^@mozaiks\/chat-ui\/workspace$|ChatUIContext$/ }, args => ({ path: args.path, namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({ contents: args.path.includes('ChatUIContext')
        ? "export const useChatUI=()=>({user:{id:'owner',app_id:'studio-host'},config:{appId:'studio-host'},auth:{getAccessToken:()=> 'fixture-token'}});"
        : 'export const WorkspaceLayout=({children})=>children;' }));
    } }],
  });
  const stylesheet = path.join(shell, 'styles.css');
  const styles = await postcss([tailwindcss()]).process((await fs.readFile(stylesheet, 'utf8'))
    + '\n@source "' + pages.replaceAll('\\', '/') + '";\n@source "' + path.join(root, 'chat-ui/src/ui').replaceAll('\\', '/') + '";', { from: stylesheet });
  let payload = billing;
  let pending = null;
  let outcome = 'proposal';
  const posts = [];
  const auth = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/api/')) {
      auth.push(req.headers.authorization);
      res.setHeader('Content-Type', 'application/json');
      if (req.method === 'POST') {
        let text = '';for await (const chunk of req) text += chunk;
        const body = JSON.parse(text);posts.push({ path: url.pathname, body });
        if (url.pathname.endsWith('/refinement/pause')) { res.end(JSON.stringify({ status: 'paused', chat_id: body.chat_id }));return; }
        if (url.pathname.endsWith('/decisions/resolve')) { pending = null;res.end(JSON.stringify({ session_state: {} }));return; }
        if (outcome === 'stale') { res.statusCode = 409;res.end(JSON.stringify({ detail: 'The selected build changed. Refresh settings before editing.' }));return; }
        if (outcome === 'staged') { res.end(JSON.stringify({ execution_mode: 'coding_worker', coding_worker: { status: 'validated', metadata: { build_record_id: 'candidate' } } }));return; }
        if (body.trigger_payload.harness_action) { pending = null;res.end(JSON.stringify({ execution_mode: 'workflow', chat_id: 'saved-edit', workflow_id: 'SubscriptionContractDesigner' }));return; }
        pending = { decision_id: 'change-one', change_request_id: 'change-one', revision_id: 'revision-one', decision_type: 'workflow_reentry',
          message: 'Review the proposed customer-plan edit.', rationale: 'Only the selected contract and dependent artifacts refresh.',
          metadata: { settings_selection: body.settings_selection }, trigger_payload: body.trigger_payload,
          actions: [{ action_id: 'confirm_recommended_workflow', label: 'Continue edit', action_type: 'run_workflow' }] };
        res.end(JSON.stringify({ execution_mode: 'harness_decision', harness_decision: pending }));return;
      }
      if (url.pathname === '/api/session/state') { res.end(JSON.stringify({ session_state: { pending_harness_decision: pending } }));return; }
      res.end(JSON.stringify(payload));return;
    }
    res.setHeader('Content-Type', url.pathname === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(url.pathname === '/fixture.js' ? bundle.outputFiles[0].text : '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>' + styles.css + '</style><div id="root"></div><script type="module" src="/fixture.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections();server.close(resolve); }));
  const browser = await chromium.launch();t.after(() => browser.close());
  const origin = 'http://127.0.0.1:' + server.address().port;
  for (const width of [390, 1280]) await t.test(width + 'px', async () => {
    payload = billing;pending = null;outcome = 'proposal';posts.length = 0;
    const page = await browser.newPage({ viewport: { width, height: 844 } });page.setDefaultTimeout(5000);
    const errors = [];page.on('pageerror', error => errors.push(error.message));
    try {
      await page.goto(origin + '/apps/first-dashboard/billing');
      await expect(page.getByRole('heading', { name: 'Subscription', exact: true })).toBeVisible();
      await expect(page.getByText('Plans in selected build', { exact: true })).toBeVisible();
      await expect(page.getByText('$0.00 / month', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Edit subscription', exact: true }).click();
      await page.getByRole('textbox').fill('Change the customer plan.');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (process.env.STUDIO_SETTINGS_EVIDENCE_DIR) {
        await fs.mkdir(process.env.STUDIO_SETTINGS_EVIDENCE_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.STUDIO_SETTINGS_EVIDENCE_DIR, 'subscription-' + width + '.png'), fullPage: true });
      }
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      assert.equal(posts.length, 0);
      await expect(page).toHaveURL(/first-dashboard\/billing$/);
      await page.getByRole('button', { name: 'Edit subscription', exact: true }).click();
      await page.getByRole('textbox').fill('Change the customer plan.');
      await page.getByRole('button', { name: 'Prepare edit', exact: true }).click();
      await expect(page.getByText('Review the proposed customer-plan edit.', { exact: true })).toBeVisible();
      assert.deepEqual(posts[0].body.settings_selection, selection);
      assert.equal(posts[0].body.app_id, 'studio-host');
      assert.equal(posts[0].body.build_registry_id, 'first-registry');
      assert.equal(posts[0].body.trigger_payload.refinement_request.artifact_version_id, 'selected-contract');
      assert.equal(posts[0].body.workflow_id, undefined);
      await page.reload();
      await expect(page.getByRole('button', { name: 'Cancel edit', exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Cancel edit', exact: true }).click();
      assert.deepEqual(posts.at(-1), { path: '/api/session/decisions/resolve', body: { decision_id: 'change-one', accepted: false, build_registry_id: 'first-registry' } });
      await expect(page).toHaveURL(/first-dashboard\/billing$/);
      await page.getByRole('button', { name: 'Edit subscription', exact: true }).click();
      await page.getByRole('textbox').fill('Change the customer plan.');
      await page.getByRole('button', { name: 'Prepare edit', exact: true }).click();
      await page.getByRole('button', { name: 'Continue edit', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'Saved workflow session' })).toBeVisible();
      const query = new URL(page.url()).searchParams;
      assert.equal(query.get('workflow'), 'SubscriptionContractDesigner');assert.equal(query.get('chat_id'), 'saved-edit');assert.equal(query.get('app_id'), 'studio-host');
      const beforeResume = posts.length;
      payload = { status: 'edit_in_progress', dashboard_app_id: 'first-dashboard', app_name: 'First app', resume: { chat_id: 'saved-edit', workflow_id: 'SubscriptionContractDesigner', app_id: 'studio-host', kind: 'subscription' } };
      await page.goto(origin + '/apps/first-dashboard/billing');
      await page.getByRole('button', { name: 'Pause edit', exact: true }).click();
      await expect(page.getByText('Edit paused. Resume its saved session when ready.', { exact: true })).toBeVisible();
      assert.deepEqual(posts.at(-1), { path: '/api/studio/apps/first-dashboard/refinement/pause', body: { chat_id: 'saved-edit', kind: 'subscription' } });
      await expect(page).toHaveURL(/first-dashboard\/billing$/);
      await page.getByRole('link', { name: 'Resume edit', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'Saved workflow session' })).toBeVisible();assert.equal(posts.length, beforeResume + 1);
      payload = billing;outcome = 'stale';
      await page.goto(origin + '/apps/first-dashboard/billing');
      await page.getByRole('button', { name: 'Edit subscription', exact: true }).click();
      await page.getByRole('textbox').fill('Update the price.');await page.getByRole('button', { name: 'Prepare edit', exact: true }).click();
      await expect(page.getByText('The selected build changed. Refresh settings before editing.', { exact: true })).toBeVisible();
      await expect(page).toHaveURL(/first-dashboard\/billing$/);
      payload = { ...billing, settings_refinement: undefined };
      await page.reload();await expect(page.getByRole('heading', { name: 'Subscription', exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Edit subscription', exact: true })).toHaveCount(0);
      payload = { status: 'configured', dashboard_app_id: 'first-dashboard', app_name: 'First app', theme: { theme: { primary: '#123456', font: 'Inter' } }, settings_refinement: { ...descriptor, artifact_kind: 'app_bundle', artifact_key: 'app_bundle', selection: { ...selection, kind: 'branding', artifact_version_id: 'selected-bundle' } } };
      pending = { decision_id: 'subscription-pending', metadata: { settings_selection: selection } };
      await page.goto(origin + '/apps/first-dashboard/branding');
      await expect(page.getByRole('link', { name: 'Open pending edit', exact: true })).toHaveAttribute('href', '/apps/first-dashboard/billing');
      await expect(page.getByRole('button', { name: 'Cancel edit', exact: true })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Edit branding', exact: true })).toHaveCount(0);
      pending = null;outcome = 'staged';
      await page.reload();
      await expect(page.getByText('#123456', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Edit branding', exact: true }).click();
      await page.getByRole('textbox').fill('Use a darker primary color.');await page.getByRole('button', { name: 'Prepare edit', exact: true }).click();
      await expect(page.getByRole('link', { name: 'Review saved builds', exact: true })).toHaveAttribute('href', '/apps/first-dashboard/activity');
      assert.equal(posts.at(-1).body.settings_selection.artifact_version_id, 'selected-bundle');
      assert.ok(posts.every(post => !/accept|promote|publish|activate/.test(post.path)));
      assert.deepEqual(errors, []);
    } catch (error) { throw new Error(error.message + "\nBrowser errors: " + JSON.stringify(errors) + "\nPage: " + (await page.locator('body').innerText())); } finally { await page.close(); }
  });
  assert.ok(auth.length > 0 && auth.every(value => value === 'Bearer fixture-token'));
});
