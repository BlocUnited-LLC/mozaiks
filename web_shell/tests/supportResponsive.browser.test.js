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

test('requester and app support inbox retain usable reply fields on mobile and desktop', async t => {
  const bundle = await build({
    stdin: { contents: `
      import React from 'react';
      import {createRoot} from 'react-dom/client';
      import {BrowserRouter, Routes, Route} from 'react-router-dom';
      import UserSupportPanel from ${JSON.stringify(path.join(pages, 'UserSupportPanel.jsx'))};
      import AppSupportPage from ${JSON.stringify(path.join(pages, 'AppSupportPage.jsx'))};
      window.actions=[];
      window.requests=[{request_id:'ticket',subject:'Account issue',subject_app_id:'notes',
        subject_app_name:'Notes',status:'open',message_thread_id:'thread',
        messages:[{role:'user',content:'Please help'}]}];
      createRoot(document.getElementById('root')).render(<BrowserRouter><Routes>
        <Route path="/me" element={<UserSupportPanel data={{requests:window.requests}}/>}/>
        <Route path="/apps/:appId/support" element={<AppSupportPage/>}/>
      </Routes></BrowserRouter>);
    `, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
    nodePaths: [path.join(shell, 'node_modules')],
    loader: { '.js': 'jsx', '.css': 'empty', '.png': 'dataurl' },
    alias: {
      react: path.join(shell, 'node_modules/react'),
      'react-dom': path.join(shell, 'node_modules/react-dom'),
      'react-router-dom': path.join(shell, 'node_modules/react-router-dom'),
      '@mozaiks/chat-ui/ui': path.join(root, 'chat-ui/src/ui/index.js'),
    },
    plugins: [{ name: 'support-boundaries', setup(builder) {
      builder.onResolve({ filter: /studioApi\.js$|StudioShared\.jsx$|AppStudioChrome\.jsx$|^@mozaiks\/chat-ui\/workspace$/ }, args => ({ path: args.path, namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({ contents:
        args.path.endsWith('studioApi.js') ? `
          export async function studioModuleAction(module,action,params){
            window.actions.push({module,action,params});
            return {success:true};
          }
          export async function studioFetch(url,options){
            const params=JSON.parse(options?.body||'{}');
            window.actions.push({url,params});
            if(url.endsWith('add_support_message')) window.requests[0].messages.push({role:'operator',content:params.message});
            return {ok:true,json:async()=>({success:true,requests:window.requests})};
          }` : args.path.endsWith('StudioShared.jsx') ? `
          export const Panel=({children})=>children;
          export const StatusPill=({children})=>children;
          export const StudioLoadingState=()=>null;
          export const StudioErrorState=()=>null;
        ` : args.path.endsWith('AppStudioChrome.jsx') ? `
          export const WorkspaceStudioHero=()=>null;
          export const formatCompactNumber=value=>String(value);
        ` : 'export const WorkspaceLayout=({children})=>children;', loader: 'jsx', resolveDir: shell }));
    } }],
  });
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell, 'styles.css'), 'utf8'))
      + `\n@source "${path.join(root, 'chat-ui/src/ui').replaceAll('\\', '/')}";`
      + `\n@source "${pages.replaceAll('\\', '/')}";`,
    { from: path.join(shell, 'styles.css') },
  );
  const html = `<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>${styles.css}</style></head><body style="margin:0;padding:16px"><div id="root"></div><script type="module" src="/fixture.js"></script></body></html>`;
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  for (const width of [360, 390, 1440]) {
    for (const route of ['/me?tab=support-tickets&request_id=ticket', '/apps/notes/support']) {
      await t.test(`${width}px ${route}`, async () => {
        const page = await browser.newPage({ viewport: { width, height: 900 } });
        page.setDefaultTimeout(5000);
        await page.goto(`http://127.0.0.1:${server.address().port}${route}`);
        if (route.startsWith('/apps/')) await page.getByRole('button', { name: /^All / }).click();
        const input = page.locator('textarea');
        await expect(input).toBeVisible();
        const bounds = await input.boundingBox();
        assert.ok(bounds.width > 120, `reply width ${bounds.width}px must remain usable`);
        assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
        await input.fill('Usable support reply');
        await page.getByRole('button', { name: 'Send', exact: true }).click();
        await expect(page.getByText('Usable support reply', { exact: true })).toBeVisible();
        const actions = await page.evaluate(() => window.actions);
        assert.ok(actions.some(action => action.params?.request_id === 'ticket' && action.params?.message === 'Usable support reply'));
        await page.close();
      });
    }
  }
});
