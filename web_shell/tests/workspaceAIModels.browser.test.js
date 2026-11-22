import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { chromium, expect } from '@playwright/test';
import { build } from 'esbuild';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);

test('workspace AI model settings load, validate and save with the authenticated policy API', async t => {
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React from 'react';
      import {createRoot} from 'react-dom/client';
      import Page from ${JSON.stringify(path.join(root, 'factory_app/app/admin/pages/WorkspaceAIModelsPage.jsx'))};
      window.mozaiksAuth = { getAccessToken: () => 'fixture-token' };
      createRoot(document.getElementById('root')).render(<Page />);
    ` },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'workspace-boundary', setup(builder) {
      builder.onResolve({ filter: /^@mozaiks\/chat-ui\/workspace$|StudioShared\.jsx$/ }, args => ({ path: args.path, namespace: 'stub' }));
      builder.onLoad({ filter: /.*/, namespace: 'stub' }, args => ({
        contents: args.path.endsWith('StudioShared.jsx')
          ? 'import React from "react"; export const ActionButton = props => <button {...props} />;'
          : 'import React from "react"; export const WorkspaceLayout = ({children}) => <div>{children}</div>;',
        loader: 'jsx',
      }));
    } }],
  });
  const requests = [];
  let rejectSave = false;
  const server = http.createServer(async (request, response) => {
    if (request.url === '/api/studio/settings/ai-models') {
      let body = '';
      for await (const chunk of request) body += chunk;
      requests.push({ method: request.method, authorization: request.headers.authorization, body: body && JSON.parse(body) });
      response.setHeader('Content-Type', 'application/json');
      if (request.method === 'PUT' && rejectSave) {
        response.statusCode = 400;
        response.end(JSON.stringify({ detail: 'Model policy rejected' }));
      } else {
        response.end(JSON.stringify({
          default: 'first', allowed: ['first'], models: [
            { id: 'first', label: 'First model', configured: true },
            { id: 'second', label: 'Second model', configured: true },
            { id: 'missing', label: 'Missing model', configured: false },
          ],
        }));
      }
      return;
    }
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text : '<div id="root"></div><script type="module" src="/fixture.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  t.after(() => page.close());
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/settings/ai-models`);
  await expect(page.getByRole('heading', { name: 'AI models' })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: /Missing model/ })).toBeDisabled();
  const save = page.getByRole('button', { name: 'Save settings' });
  await expect(save).toBeDisabled();
  await page.getByRole('checkbox', { name: /Second model/ }).check();
  await page.getByLabel('Default model').selectOption('second');
  await page.getByRole('checkbox', { name: /First model/ }).uncheck();
  await save.click();
  await expect(page.getByRole('status')).toHaveText('AI model settings saved.');
  assert.deepEqual(requests.map(({ method, authorization }) => ({ method, authorization })), [
    { method: 'GET', authorization: 'Bearer fixture-token' },
    { method: 'PUT', authorization: 'Bearer fixture-token' },
  ]);
  assert.deepEqual(requests[1].body, { default: 'second', allowed: ['second'] });
  await expect(save).toBeDisabled();
  rejectSave = true;
  await page.getByRole('checkbox', { name: /First model/ }).check();
  await save.click();
  await expect(page.getByRole('alert')).toHaveText('Model policy rejected');
  await expect(save).toBeEnabled();
  assert.deepEqual(errors, []);
});
