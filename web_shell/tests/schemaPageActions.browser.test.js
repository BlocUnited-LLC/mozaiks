import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.dirname(shell);
const screenshotDir = process.env.MOZAIKS_TEST_SCREENSHOTS;
if (screenshotDir) await fs.mkdir(screenshotDir, { recursive: true });
const styles = postcss([tailwindcss()]).process(
  (await fs.readFile(path.join(shell, 'styles.css'), 'utf8'))
    + `\n@source "${path.join(root, 'chat-ui/src/ui').replaceAll('\\', '/')}";`,
  { from: path.join(shell, 'styles.css') },
).then(async result => result.css + '\n' + await fs.readFile(path.join(root, 'chat-ui/src/components/layout/header-styles.css'), 'utf8'));

for (const tablePrimitive of ['ResourceTable', 'DataTable']) {
test(`${tablePrimitive} CRUD carries selection, authenticates and keeps failed dialogs open`, async (t) => {
  const requests = [];
  let rejectDelete = true;
  let rejectList = tablePrimitive === 'DataTable';
  const modulePath = '/api/modules/records/';
  const pageSchema = {
    name: 'records', title: 'Records', layout: 'full-width', sections: [
      { id: 'summary', primitive: 'SummaryStrip', config: {
        api_endpoint: modulePath + 'summary', items: [{ label: 'My records', value_key: 'total' }],
      } },
      { id: 'header', primitive: 'PageHeader', config: { title: 'Records', actions: [
        { id: 'add', label: 'Add record', action_type: 'event', event_type: 'ui.modal.open', payload: { modal_id: 'create' } },
      ] } },
      { id: 'table', primitive: tablePrimitive, config: {
        api_endpoint: modulePath + 'list', data_key: 'records', columns: [{ key: 'name', label: 'Name', sortable: true }, { key: 'notes', label: 'Notes' }], selection: 'single',
        search: true,
        search_keys: ['name', 'email'],
        ...(tablePrimitive === 'ResourceTable' ? { search_placeholder: 'Find records', search_keys: ['email'] } : {}),
        actions: [
          { id: 'details', label: 'Details', requires_selection: true, action_type: 'event', event_type: 'ui.modal.open', payload: { modal_id: 'details' } },
          { id: 'edit', label: 'Edit', requires_selection: true, action_type: 'event', event_type: 'ui.modal.open', payload: { modal_id: 'edit' } },
          { id: 'delete', label: 'Delete', requires_selection: true, action_type: 'event', event_type: 'ui.modal.open', payload: { modal_id: 'delete' } },
        ],
      } },
      { id: 'details', primitive: 'Modal', config: { title: 'Record details', children: [
        { id: 'details-form', primitive: 'Form', config: { initial_values_key: 'selected_row', disabled: true,
          fields: [{ name: 'name', label: 'Name', type: 'text' }] } },
      ] } },
      ...['create', 'edit'].map((id) => ({ id, primitive: 'Modal', config: { title: id === 'create' ? 'Create record' : 'Edit record', children: [
        { id: id + '-form', primitive: 'Form', config: {
          initial_values_key: id === 'edit' ? 'selected_row' : null,
          fields: [
            { name: 'name', label: 'Name', type: 'text', required: true },
            { name: 'notes', label: 'Notes', type: 'textarea', default_value: '' },
            { name: 'status', label: 'Status', type: 'select', default_value: 'planned', options: [{ value: 'planned', label: 'Planned' }, { value: 'complete', label: 'Complete' }] },
            { name: 'count', label: 'Count', type: 'number', default_value: 0 },
            { name: 'notify', label: 'Notify', type: 'checkbox', default_value: true },
          ],
          submit_label: 'Save', submit_action: { label: 'Save', action_type: 'submit', href: modulePath + id, closes_modal: true,
            payload: id === 'create' ? null : { record_id: '{selected_row.record_id}', name: '{form.name}', notes: '{form.notes}' } },
        } },
      ] } })),
      { id: 'delete', primitive: 'Modal', config: { title: 'Confirm deletion', description: 'Delete {selected_row.name}?', actions: [
        { id: 'confirm', label: 'Confirm delete', action_type: 'delete', href: modulePath + 'delete', payload: { record_id: '{selected_row.record_id}' }, closes_modal: true },
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
    jsx: 'automatic', loader: { '.js': 'jsx' }, nodePaths: [path.join(shell, 'node_modules')],
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
    if (req.url === '/fixture.css') {
      res.setHeader('Content-Type', 'text/css');
      res.end(await styles);
      return;
    }
    if (req.url.startsWith(modulePath)) {
      let body = ''; for await (const chunk of req) body += chunk;
      requests.push({ path: req.url, method: req.method, authorization: req.headers.authorization, body: body ? JSON.parse(body) : null });
      res.setHeader('Content-Type', 'application/json');
      if (req.url.endsWith('/list') && rejectList) { res.statusCode = 404; rejectList = false; res.end('{}'); return; }
      if (req.url.endsWith('/delete') && rejectDelete) { res.statusCode = 409; rejectDelete = false; res.end('{}'); return; }
      res.end(JSON.stringify(req.url.endsWith('/list') ? { records: [
        { record_id: 'r1', name: 'Ada', notes: 'Original note', email: 'first@example.test', status: 'complete', count: 4, notify: false },
        { record_id: 'r2', name: 'Zoe', notes: 'Another note', email: 'zoe@example.test' },
      ] } : req.url.endsWith('/summary') ? { total: 7 } : { success: true }));
      return;
    }
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text : '<html><head><link rel="stylesheet" href="/fixture.css"></head><body><div id="root"></div><nav class="shell-mobile-bottom-bar"><button>Navigation</button></nav><script src="/fixture.js"></script></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByText('7', { exact: true }).waitFor();
  if (tablePrimitive === 'DataTable') {
    await page.getByText('Unable to load records', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Retry', exact: true }).click();
  }
  try {
    await page.getByRole('cell', { name: 'Ada', exact: true }).waitFor({timeout: 5000});
  } catch (error) {
    t.diagnostic(JSON.stringify({ errors, requests, snapshot: await page.locator('body').ariaSnapshot() }));
    throw error;
  }
  await page.getByRole('button', { name: 'Add record', exact: true }).click();
  assert.equal(await page.getByRole('dialog').getByLabel('Status', { exact: true }).getByText('Planned', { exact: true }).isVisible(), true);
  assert.equal(await page.getByRole('dialog').getByLabel('Count', { exact: true }).inputValue(), '0');
  assert.equal(await page.getByRole('dialog').getByLabel('Notify', { exact: true }).isChecked(), true);
  assert.equal(await page.getByRole('dialog').getByLabel('Notes', { exact: true }).inputValue(), '');
  if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, `${tablePrimitive}-defaults-desktop.png`) });
  await page.getByRole('dialog').getByLabel('Name').fill('   ');
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByText('Name is required', { exact: true }).waitFor();
  assert.equal(requests.filter(request => request.path.endsWith('/create')).length, 0);
  await page.getByRole('dialog').getByLabel('Name').fill('Grace');
  await page.getByRole('dialog').getByLabel('Notes').fill('Line one\nLine two');
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  assert.deepEqual(requests.find(r => r.path.endsWith('/create')).body, { name: 'Grace', notes: 'Line one\nLine two', status: 'planned', count: 0, notify: true });
  await page.getByRole('cell', { name: 'Ada', exact: true }).click();
  await page.getByRole('columnheader', { name: 'Name', exact: true }).click();
  await page.getByRole('columnheader', { name: /^Name/ }).click();
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  assert.equal(await page.getByRole('dialog').getByLabel('Name').inputValue(), 'Ada');
  assert.equal(await page.getByRole('dialog').getByLabel('Notes').inputValue(), 'Original note');
  assert.equal(await page.getByRole('dialog').getByLabel('Status', { exact: true }).getByText('Complete', { exact: true }).isVisible(), true);
  assert.equal(await page.getByRole('dialog').getByLabel('Count', { exact: true }).inputValue(), '4');
  assert.equal(await page.getByRole('dialog').getByLabel('Notify', { exact: true }).isChecked(), false);
  await page.getByRole('dialog').getByLabel('Name').fill('Ada updated');
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  assert.deepEqual(requests.find(r => r.path.endsWith('/edit')).body, { record_id: 'r1', name: 'Ada updated', notes: 'Original note' });
  await page.getByRole('cell', { name: 'Ada', exact: true }).click();
  await page.getByRole('button', { name: 'Details', exact: true }).click();
  assert.equal(await page.getByRole('dialog').getByLabel('Name').inputValue(), 'Ada');
  assert.equal(await page.getByRole('dialog').getByLabel('Name').isDisabled(), true);
  assert.equal(await page.getByRole('dialog').getByRole('button', { name: 'Submit', exact: true }).count(), 0);
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await page.getByRole('dialog').getByText('Delete Ada?', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Confirm delete', exact: true }).click();
  await page.getByRole('dialog').getByRole('alert').waitFor();
  await page.getByRole('button', { name: 'Confirm delete', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  const deletes = requests.filter(r => r.path.endsWith('/delete'));
  assert.equal(deletes.length, 2);
  assert.ok(deletes.every(r => r.method === 'POST' && r.body.record_id === 'r1'));
  const search = page.getByRole('searchbox', { name: tablePrimitive === 'ResourceTable' ? 'Find records' : 'Search...' });
  const listRequests = requests.filter(r => r.path.endsWith('/list')).length;
  await search.fill('Original note');
  await page.getByText('No results', { exact: true }).waitFor();
  if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, `${tablePrimitive}-excluded-notes.png`) });
  if (tablePrimitive === 'ResourceTable') {
    await search.fill('first@example.test');
    await page.getByRole('cell', { name: 'Ada', exact: true }).waitFor();
    assert.equal(await page.getByRole('cell', { name: 'Zoe', exact: true }).count(), 0);
    await search.fill('Ada');
    await page.getByText('No results', { exact: true }).waitFor();
  } else {
    await search.fill('first@example.test');
    await page.getByRole('cell', { name: 'Ada', exact: true }).waitFor();
    assert.equal(await page.getByRole('cell', { name: 'Zoe', exact: true }).count(), 0);
    await search.fill('Zoe');
    await page.getByRole('cell', { name: 'Zoe', exact: true }).waitFor();
    assert.equal(await page.getByRole('cell', { name: 'Ada', exact: true }).count(), 0);
  }
  assert.equal(requests.filter(r => r.path.endsWith('/list')).length, listRequests);
  assert.equal(await page.getByRole('button', { name: 'Delete', exact: true }).isDisabled(), true);
  await search.fill('');
  for (const height of [844, 500]) {
    await page.setViewportSize({ width: 390, height });
    await page.getByRole('button', { name: 'Add record', exact: true }).click();
    const dialog = page.getByRole('dialog');
    assert.equal(await dialog.getByLabel('Status', { exact: true }).getByText('Planned', { exact: true }).isVisible(), true);
    assert.equal(await dialog.getByLabel('Count', { exact: true }).inputValue(), '0');
    assert.equal(await dialog.getByLabel('Notify', { exact: true }).isChecked(), true);
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, `${tablePrimitive}-defaults-mobile-${height}.png`) });
    await dialog.getByLabel('Name').fill('Mobile');
    await dialog.getByLabel('Notes').fill('Mobile notes');
    const bounds = await dialog.boundingBox();
    assert.ok(bounds.height <= height, JSON.stringify(bounds));
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
  }
  assert.equal(requests.filter(r => r.path.endsWith('/create')).length, 3);
  assert.ok(requests.every(r => r.authorization === 'Bearer test-session'));
  assert.deepEqual(errors, []);
});
}
