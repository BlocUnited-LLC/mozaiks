import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';

// A module action validates the submitted body against its declared
// input_schema. The Form primitive renders a `number` field as a text input
// and a `select` as string options, so without coercion a typed price reaches
// create_product as "12.5" and the runtime rejects the whole submit. The
// commerce products page and every constructed create/edit modal depend on
// the body carrying the declared types.
const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);

test('Form submits number, select and checkbox fields with their declared types', async (t) => {
  const requests = [];
  const modulePath = '/api/modules/commerce/';
  const pageSchema = {
    name: 'products', title: 'Products', layout: 'full-width', sections: [
      { id: 'header', primitive: 'PageHeader', config: { title: 'Products', actions: [
        { id: 'new-product', label: 'New Product', action_type: 'event', event_type: 'ui.modal.open', payload: { modal_id: 'create-product-modal' } },
      ] } },
      { id: 'create-product-modal', primitive: 'Modal', config: { title: 'New Product', children: [
        { id: 'create-product-form', primitive: 'Form', config: {
          fields: [
            { name: 'title', label: 'Title', type: 'text', required: true },
            { name: 'status', label: 'Status', type: 'select', options: [{ value: 'draft', label: 'Draft' }, { value: 'active', label: 'Active' }] },
            { name: 'price_amount', label: 'Price Amount', type: 'number', required: true },
            { name: 'inventory_quantity', label: 'Inventory Quantity', type: 'number' },
            { name: 'priority', label: 'Priority', type: 'select', options: [{ value: 1, label: 'High' }, { value: 2, label: 'Low' }] },
            { name: 'track_inventory', label: 'Track Inventory', type: 'checkbox' },
          ],
          submit_label: 'Create Product',
          submit_action: { label: 'Create Product', action_type: 'submit', href: modulePath + 'create_product', closes_modal: true },
        } },
      ] } },
    ],
  };
  const entry = `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import {PageRenderer} from ${JSON.stringify(path.join(root, 'chat-ui/src/ui/page-renderer/index.js'))};
    createRoot(document.getElementById('root')).render(<PageRenderer schema={${JSON.stringify(pageSchema)}} />);
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' }, bundle: true, write: false,
    jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' }, nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'test-boundaries', setup(build) {
      build.onResolve({ filter: /(?:adapters\/api\.js|hooks\/useWorkflowStart\.js)$/ }, args => ({ path: args.path, namespace: 'test-boundary' }));
      build.onLoad({ filter: /.*/, namespace: 'test-boundary' }, args => ({
        contents: args.path.includes('useWorkflowStart')
          ? 'export const useWorkflowStart = () => ({startWorkflow: async () => {}});'
          : 'export const authFetch = (url, options={}) => fetch(url, {...options, headers:{...options.headers, Authorization:"Bearer test-session"}});',
      }));
    } }],
  });
  const server = http.createServer(async (req, res) => {
    if (req.url.startsWith('/api/modules/')) {
      let body = ''; for await (const chunk of req) body += chunk;
      requests.push({ path: req.url, body: body ? JSON.parse(body) : null });
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ success: true, product: { product_id: 'p1' } }));
      return;
    }
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text : '<html><head></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { await browser.close(); server.close(); });
  t.after(() => {
    if (t.passed === false) t.diagnostic(JSON.stringify({ errors, requests }));
  });
  await page.goto(`http://127.0.0.1:${server.address().port}`);

  await page.getByRole('button', { name: 'New Product', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor();
  await dialog.getByLabel('Title').fill('Widget');
  await dialog.getByLabel('Status', { exact: true }).click();
  await page.getByRole('option', { name: 'Active' }).click();
  await dialog.getByLabel('Price Amount').fill('12.5');
  await dialog.getByLabel('Priority', { exact: true }).click();
  await page.getByRole('option', { name: 'Low' }).click();
  await dialog.getByLabel('Track Inventory').check();
  await dialog.getByRole('button', { name: 'Create Product', exact: true }).click();
  await page.waitForTimeout(300);

  const created = requests.find(request => request.path.endsWith('/create_product'));
  assert.ok(created, 'the submit reached the module action');
  assert.deepEqual(created.body, {
    title: 'Widget', status: 'active', price_amount: 12.5, priority: 2, track_inventory: true,
  });
  assert.equal('inventory_quantity' in created.body, false, 'an empty optional number is left out of the body');
  assert.deepEqual(errors, []);
});
