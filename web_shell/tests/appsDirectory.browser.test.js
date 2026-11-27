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
const component = path.join(root, 'factory_app/app/admin/pages/AppsPage.jsx');

async function fixture(t) {
  const bundle = await build({
    stdin: { contents: `
      import React,{useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import {BrowserRouter} from 'react-router-dom';
      import {AppsDirectory} from ${JSON.stringify(component)};
      const first={build_registry_id:'first',app_id:'app-first',name:'First app',status:'building'};
      const second={build_registry_id:'second',app_id:'app-second',name:'Second app',status:'active'};
      function Fixture(){
        const [state,setState]=useState({apps:[first],page:1,search:'',filter:'all',loading:false,error:null,paged:true});
        window.updateDirectory=update=>setState(value=>({...value,...update}));
        const pagination=state.paged ? {page:state.page,hasNext:state.page===1,hasPrevious:state.page===2,
          onNext:()=>setState(value=>({...value,apps:[second],page:2})),
          onPrevious:()=>setState(value=>({...value,apps:[first],page:1})),
          onRetry:()=>setState(value=>({...value,error:null,apps:[second]})),
        } : undefined;
        return <AppsDirectory {...state} pagination={pagination}
          {...(state.paged?{searchValue:state.search,activeFilter:state.filter,
            onSearchChange:search=>setState(value=>({...value,search})),
            onFilterChange:filter=>setState(value=>({...value,filter}))}:{})}
          deleteApp={state.allowDelete ? ()=>{throw new Error('Unexpected deletion')} : undefined}
          navigationForApp={state.stock ? undefined : app=>({primaryAction:{kind:'overview',href:'/apps/'+app.build_registry_id+'/overview'},
            dashboardHref:Object.hasOwn(app,'managementUrl') ? app.managementUrl : '/apps/'+app.build_registry_id+'/overview'})}/>;
      }
      createRoot(document.getElementById('root')).render(<BrowserRouter><Fixture/></BrowserRouter>);
    `, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: {
      react: path.join(shell, 'node_modules/react'),
      'react-dom': path.join(shell, 'node_modules/react-dom'),
      'react-router-dom': path.join(shell, 'node_modules/react-router-dom'),
      '@mozaiks/chat-ui/ui': path.join(root, 'chat-ui/src/ui/index.js'),
      '@mozaiks/chat-ui/admin': path.join(root, 'chat-ui/src/admin'),
    },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'host-boundaries', setup(builder) {
      builder.onResolve({ filter: /^(?:@mozaiks\/chat-ui\/workspace)$|(?:studioApi|useWorkspaceApps|useWorkflowStart)\.js$/ }, args => ({ path: args.path, namespace: 'host-boundary' }));
      builder.onLoad({ filter: /.*/, namespace: 'host-boundary' }, args => ({
        contents: args.path.endsWith('studioApi.js')
          ? `export const studioFetch=async()=>new Response(JSON.stringify({app:{scope:'app',default_portal:'overview',
                portals:[{id:'overview',route:window.testDashboardRoute || '/apps/:appId/overview'}]}}));`
          : args.path.endsWith('useWorkspaceApps.js')
            ? 'export const useWorkspaceApps=()=>({apps:[]});'
            : args.path.endsWith('useWorkflowStart.js')
              ? 'export const useWorkflowStart=()=>({startWorkflow:async()=>null,starting:false,error:null});'
              : `export const WorkspaceLayout=({children})=>children;
                 export const useWorkspaceAppSections=()=>[{key:'integrations',label:'Integrations',route:'/apps/:appId/integrations'}];`,
      }));
    } }],
  });
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell, 'styles.css'), 'utf8'))
      + `\n@source "${path.join(root, 'chat-ui/src/ui').replaceAll('\\', '/')}";`
      + `\n@source "${path.dirname(component).replaceAll('\\', '/')}";`,
    { from: path.join(shell, 'styles.css') },
  );
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${styles.css}</style></head><body style="margin:0;padding:16px"><div id="root"></div><script type="module" src="/fixture.js"></script></body></html>`;
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  return page;
}

test('AppsDirectory accepts server pages, controlled search/filters, retry and bounded navigation on mobile', async t => {
  const page = await fixture(t);
  await page.getByText('First app', { exact: true }).filter({ visible: true }).waitFor();
  await page.getByRole('heading', { name: 'My Apps', exact: true }).waitFor();
  await page.getByText('On this page', { exact: true }).waitFor();
  assert.equal(await page.getByText('Total', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Previous', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await page.getByText('Page 2', { exact: true }).waitFor();
  await page.getByText('Second app', { exact: true }).filter({ visible: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Next', exact: true }).isDisabled(), true);
  const search = page.getByRole('searchbox', { name: 'Search app names and descriptions...' });
  await search.fill('server controls these results');
  await page.getByRole('button', { name: 'Needs input', exact: true }).click();
  assert.equal(await page.getByText('Second app', { exact: true }).filter({ visible: true }).count(), 1, 'server rows must not be filtered a second time');
  await page.evaluate(() => window.updateDirectory({ loading: true }));
  await expect(page.getByRole('button', { name: 'Previous', exact: true })).toBeDisabled();
  assert.equal(await search.inputValue(), 'server controls these results');
  await page.evaluate(() => window.updateDirectory({ loading: false, error: 'Temporary outage' }));
  await page.getByText('Temporary outage', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Try again', exact: true }).click();
  await page.getByText('Second app', { exact: true }).filter({ visible: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 664 });
  await expect(page.getByText(/^On this page:\s*1$/)).toBeVisible();
  await expect(page.getByLabel('Summary metrics')).toBeHidden();
  if (process.env.MOZAIKS_TEST_SCREENSHOTS) {
    await fs.mkdir(process.env.MOZAIKS_TEST_SCREENSHOTS, { recursive: true });
    await page.screenshot({
      path: path.join(process.env.MOZAIKS_TEST_SCREENSHOTS, 'paginated-apps-390x664.png'),
      fullPage: false,
    });
  }
  await page.getByRole('button', { name: 'Previous', exact: true }).click();
  await page.getByText('First app', { exact: true }).filter({ visible: true }).waitFor();
  await expect(page.getByText(/^On this page:\s*1$/)).toBeVisible();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.getByRole('button', { name: 'Dashboard', exact: true }).filter({ visible: true }).click();
  assert.ok(page.url().endsWith('/apps/first/overview'));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.evaluate(() => window.updateDirectory({ search: '', filter: 'all', apps: [
    { app_id: 'old', name: 'Older record', status: 'active', updated_at: '2020-01-01' },
    { app_id: 'new', name: 'Newer record', status: 'active', updated_at: '2026-01-01' },
  ] }));
  await page.getByText('Older record', { exact: true }).filter({ visible: true }).waitFor();
  assert.deepEqual(await page.locator('tbody tr td:first-child .font-semibold').allTextContents(), ['Older record', 'Newer record'], 'server page order must be preserved');
});

test('AppsDirectory keeps local filtering when a complete portfolio is supplied', async t => {
  const page = await fixture(t);
  await page.evaluate(() => window.updateDirectory({ paged: false }));
  const search = page.getByRole('searchbox', { name: 'Search apps...' });
  await search.fill('absent');
  await page.getByText('No apps match this search', { exact: true }).waitFor();
  await search.fill('First');
  await page.getByText('First app', { exact: true }).filter({ visible: true }).waitFor();
  assert.equal(await page.getByRole('navigation', { name: 'Apps pagination' }).count(), 0);
});

test('section chooser uses hosted management links and keeps search, pagination and keyboard navigation', async t => {
  const page = await fixture(t);
  await page.goto(new URL('/apps?section=integrations', page.url()).href);
  await page.evaluate(() => window.updateDirectory({ allowDelete: true }));
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(page.getByRole('heading', { name: 'Integrations', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Open Integrations for First app' })).toHaveAttribute('href', '/apps/first/integrations');
    assert.equal(await page.getByRole('button', { name: /Delete app|Continue Build/ }).count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  const search = page.getByRole('searchbox', { name: 'Search app names and descriptions...' });
  await search.fill('server search');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  const link = page.getByRole('link', { name: 'Open Integrations for Second app' });
  await expect(link).toHaveAttribute('href', '/apps/second/integrations');
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/apps\/second\/integrations$/);
  await page.goBack();
  await expect(page.getByRole('link', { name: 'Back to My Apps' })).toHaveAttribute('href', '/apps');
});

test('section chooser handles stock IDs, invalid host links, unavailable sections and directory states', async t => {
  const page = await fixture(t);
  await page.goto(new URL('/apps?section=integrations', page.url()).href);
  await page.evaluate(() => window.updateDirectory({ stock: true, paged: false, allowDelete: true }));
  await expect(page.getByRole('link', { name: 'Open Integrations for First app' })).toHaveAttribute('href', '/apps/app-first/integrations');
  await page.evaluate(() => window.updateDirectory({ stock: false, apps: [
    { name: 'Missing hosted ID', app_id: 'factory-1' },
    { name: 'Absent link', app_id: 'factory-2', managementUrl: null },
    { name: 'Foreign origin', app_id: 'factory-3', managementUrl: 'https://example.org/apps/other/overview' },
    { name: 'Invalid segment', app_id: 'factory-4', managementUrl: '/apps/foo%2Fbar/overview' },
    { name: 'Different local route', app_id: 'factory-5', managementUrl: '/apps/other/profile' },
  ] }));
  await expect(page.getByText('Section unavailable for this app')).toHaveCount(5);
  assert.equal(await page.getByRole('link', { name: /^Open Integrations for/ }).count(), 0);
  await page.evaluate(() => window.updateDirectory({ loading: true }));
  await expect(page.getByText('Loading your apps…')).toBeVisible();
  await page.evaluate(() => window.updateDirectory({ loading: false, error: 'Temporary directory failure' }));
  await expect(page.getByText('Could not load apps')).toBeVisible();
  await page.evaluate(() => window.updateDirectory({ error: null, apps: [] }));
  await expect(page.getByText('No apps yet')).toBeVisible();
  for (const query of ['section=operations', 'section=integrations&section=operations']) {
    await page.goto(new URL('/apps?' + query, page.url()).href);
    await expect(page.getByText('Studio section unavailable')).toBeVisible();
    assert.equal(await page.getByRole('link', { name: /^Open .* for/ }).count(), 0);
  }
  await page.getByRole('link', { name: 'Back to My Apps' }).click();
  await expect(page.getByRole('heading', { name: 'My Apps', exact: true })).toBeVisible();
});

test('section chooser resolves nested management routes and opaque dashboard IDs', async t => {
  const page = await fixture(t);
  await page.addInitScript(() => { window.testDashboardRoute = '/apps/:appId/manage/overview'; });
  await page.goto(new URL('/apps?section=integrations', page.url()).href);
  await page.evaluate(() => window.updateDirectory({ apps: [
    { name: 'Imported app', app_id: 'factory-target', managementUrl: '/apps/old.app%2Bwest%20team/manage/overview' },
    { name: 'Wrong default', app_id: 'other-target', managementUrl: '/apps/other/overview' },
  ] }));
  await expect(page.getByRole('link', { name: 'Open Integrations for Imported app' }))
    .toHaveAttribute('href', '/apps/old.app%2Bwest%20team/integrations');
  await expect(page.getByText('Section unavailable for this app')).toHaveCount(1);
});

test('AppsDirectory shows the approved name without changing draft identity or navigation', async t => {
  const page = await fixture(t);
  const record = { build_registry_id: 'same-record', app_id: 'draft-app-58c84bf6',
    name: null, name_status: 'provisional', name_source: 'provisional', status: 'building' };
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.evaluate(app => window.updateDirectory({ apps: [app] }), record);
    await page.getByText('Draft 58C84B', { exact: true }).filter({ visible: true }).waitFor();
    assert.equal(await page.getByText(record.app_id, { exact: true }).filter({ visible: true }).count(), 0);
    await page.evaluate(app => window.updateDirectory({ apps: [{ ...app, name: 'FocusSprint',
      name_status: 'named', name_source: 'value_engine_concept' }] }), record);
    await page.getByText('FocusSprint', { exact: true }).filter({ visible: true }).waitFor();
    assert.equal(await page.getByText('Draft 58C84B', { exact: true }).filter({ visible: true }).count(), 0);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  await page.getByRole('button', { name: 'Dashboard', exact: true }).filter({ visible: true }).click();
  assert.ok(page.url().endsWith('/apps/same-record/overview'));
});
