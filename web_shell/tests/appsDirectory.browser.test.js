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
          navigationForApp={app=>({primaryAction:{kind:'overview',href:'/apps/'+app.build_registry_id+'/overview'},dashboardHref:'/apps/'+app.build_registry_id+'/overview'})}/>;
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
      builder.onResolve({ filter: /^(?:@mozaiks\/chat-ui\/workspace)$|(?:studioApi|useWorkspaceApps)\.js$/ }, args => ({ path: args.path, namespace: 'host-boundary' }));
      builder.onLoad({ filter: /.*/, namespace: 'host-boundary' }, args => ({
        contents: args.path.endsWith('studioApi.js')
          ? 'export const studioFetch=async()=>({app:{portals:[]}});'
          : args.path.endsWith('useWorkspaceApps.js')
            ? 'export const useWorkspaceApps=()=>({apps:[]});'
            : 'export const WorkspaceLayout=({children})=>children;',
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
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Previous', exact: true }).click();
  await page.getByText('First app', { exact: true }).filter({ visible: true }).waitFor();
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
