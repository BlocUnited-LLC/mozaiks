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
const manifest = JSON.parse(await fs.readFile(path.join(root, 'factory_app/app/ui/route_manifest.json'), 'utf8'));

test('Studio switches between portfolio and selected-app menus on desktop and mobile', async t => {
  const bundle = await build({
    stdin: { contents: `
      import React from 'react';
      import {createRoot} from 'react-dom/client';
      import {BrowserRouter, Link} from 'react-router-dom';
      import {WorkspaceLayout, useWorkspaceAppSections} from './chat-ui/src/workspace/WorkspaceLayout.jsx';
      function Body() {
        const sections = useWorkspaceAppSections();
        return <main className="pt-24"><Link to="/apps/first/overview">First Dashboard</Link>
          <Link to="/apps/second/overview">Second Dashboard</Link>
          <output data-testid="sections">{sections.map(item => item.route).join(',')}</output></main>;
      }
      createRoot(document.getElementById('root')).render(<BrowserRouter><WorkspaceLayout><Body /></WorkspaceLayout></BrowserRouter>);
    `, loader: 'jsx', resolveDir: root },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
    nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom'),
      'react-router-dom': path.join(shell, 'node_modules/react-router-dom'), 'react-icons': path.join(shell, 'node_modules/react-icons') },
    plugins: [{ name: 'host-context', setup(builder) {
      builder.onResolve({ filter: /\/(NavigationProvider|ChatUIContext)\.jsx$/ }, args => ({ path: args.path, namespace: 'host-context' }));
      builder.onLoad({ filter: /.*/, namespace: 'host-context' }, args => ({ contents: args.path.includes('NavigationProvider')
        ? 'export const useNavigation=()=>({pages:' + JSON.stringify(manifest.pages) + ',surface:"studio"});'
        : 'export const useChatUI=()=>({user:window.fixtureUser});' }));
    } }],
  });
  const stylesheet = path.join(shell, 'styles.css');
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(stylesheet, 'utf8')) + '\n@source "' + path.join(root, 'chat-ui/src/workspace').replaceAll('\\', '/') + '";',
    { from: stylesheet },
  );
  const html = '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>' + styles.css + '</style><div id="root"></div><script type="module" src="/fixture.js"></script>';
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch();
  t.after(() => browser.close());
  const origin = 'http://127.0.0.1:' + server.address().port;
  for (const width of [390, 1280]) {
    for (const role of ['user', 'admin']) {
      await t.test(width + 'px ' + role, async () => {
        const page = await browser.newPage({ viewport: { width, height: 844 } });
        try {
          page.setDefaultTimeout(5000);
          await page.addInitScript(role => { window.fixtureUser = { id: 'owner', roles: [role] }; }, role);
          await page.goto(origin + '/apps?section=revenue');
          if (width < 1024) await page.getByRole('button', { name: 'Open Studio navigation' }).click();
          const workspace = page.getByRole('navigation', { name: 'Workspace navigation', exact: true });
          await expect(workspace.getByRole('link', { name: 'My Apps', exact: true })).toHaveAttribute('aria-current', 'page');
          await expect(workspace.getByText('Manage an app', { exact: true })).toHaveCount(0);
          await expect(workspace.getByRole('link', {name:'Support',exact:true})).toHaveCount(0);
          await expect(workspace.getByRole('link', {name:'AI models',exact:true})).toHaveCount(0);
          await expect(workspace.getByRole('link', {name:'Revenue',exact:true})).toHaveAttribute('href','/portfolio/revenue');
          assert.equal(await workspace.getByRole('link', { name: 'Project', exact: true }).count(), 0);
          assert.equal(await workspace.getByRole('link', { name: 'Token Usage', exact: true }).count(), role === 'admin' ? 1 : 0);
          await expect(page.getByTestId('sections')).toContainText('/apps/:appId/revenue');
          if (width < 1024) await page.getByRole('button', { name: 'Close Studio navigation' }).last().click();
          for (const app of ['First', 'Second']) {
            await page.getByRole('link', { name: app + ' Dashboard', exact: true }).click();
            if (width < 1024) await page.getByRole('button', { name: 'Open Studio navigation' }).click();
            const selected = page.getByRole('navigation', { name: 'App Studio navigation', exact: true });
            await expect(workspace).toHaveCount(0);
            await expect(selected.getByRole('link', { name: 'Overview', exact: true })).toHaveAttribute('aria-current', 'page');
            await expect(selected.getByRole('link', { name: 'Revenue', exact: true })).toHaveAttribute('href', '/apps/' + app.toLowerCase() + '/revenue');
            await expect(selected.getByRole('link', { name: 'Project', exact: true })).toHaveAttribute('href', '/apps/' + app.toLowerCase() + '/building');
            if (width < 1024) await page.getByRole('button', { name: 'Close Studio navigation' }).last().click();
          }
        } finally { await page.close(); }
      });
    }
  }
});
