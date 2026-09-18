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
const styles = postcss([tailwindcss()]).process(
  await fs.readFile(path.join(shell, 'styles.css'), 'utf8')
    + `\n@source "${path.join(root, 'chat-ui/src/ui').replaceAll('\\', '/')}";`,
  { from: path.join(shell, 'styles.css') },
).then(result => result.css);

async function fixture(t, { primitive = 'DataTable', serverMode = true } = {}) {
  const requests = [];
  const pending = new Map();
  let rejectOnce = true;
  let rows = Array.from({ length: 61 }, (_, index) => ({
    id: `book-${index + 1}`, title: `Book ${String(index + 1).padStart(3, '0')}`,
    author: index === 55 ? 'Remote Author' : 'Local Author', notes: 'notes-only',
  }));
  rows[54].title = 'Literal [x].* &? +';
  const schema = {
    name: 'books', title: 'Books', layout: 'full-width', sections: [{
      id: 'books', primitive, config: {
        api_endpoint: '/api/modules/books/list', data_key: 'items',
        columns: [{ key: 'title', label: 'Title', sortable: true }, { key: 'author', label: 'Author' }, { key: 'notes', label: 'Notes' }],
        pagination: true, page_size: 20, pagination_mode: serverMode ? 'server' : 'client',
        ...(serverMode ? { total_key: 'stats.total' } : {}), search: true, search_keys: ['title', 'author'], selection: 'single',
        actions: [{ id: 'delete', label: 'Delete', action_type: 'delete', requires_selection: true,
          href: '/api/modules/books/delete', payload: { id: '{selected_row.id}' } }],
      },
    }],
  };
  const bundle = await build({
    stdin: { contents: `
      import React from 'react'; import {createRoot} from 'react-dom/client';
      import {PageRenderer} from ${JSON.stringify(path.join(root, 'chat-ui/src/ui/page-renderer/index.js'))};
      createRoot(document.getElementById('root')).render(<React.StrictMode><PageRenderer schema={${JSON.stringify(schema)}} /></React.StrictMode>);
    `, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'test-boundaries', setup(build) {
      build.onResolve({ filter: /(?:adapters\/api\.js|hooks\/useWorkflowStart\.js)$/ }, args => ({ path: args.path, namespace: 'test-boundary' }));
      build.onLoad({ filter: /.*/, namespace: 'test-boundary' }, args => ({ contents: args.path.includes('useWorkflowStart')
        ? 'export const useWorkflowStart = () => ({startWorkflow: async () => {}});'
        : `export const authFetch = (url, options={}) => {
            const ignoreAbort = new URL(url, location.origin).searchParams.get('search')?.startsWith('stale-');
            return fetch(url, {...options, ...(ignoreAbort ? {signal: undefined} : {}), headers: {...options.headers, Authorization:'Bearer fixture'}});
          };`,
      }));
    } }],
  });
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://fixture');
    if (url.pathname.startsWith('/api/modules/books/')) {
      let body = ''; for await (const chunk of req) body += chunk;
      requests.push({ path: url.pathname, query: Object.fromEntries(url.searchParams), auth: req.headers.authorization });
      res.setHeader('Content-Type', 'application/json');
      if (url.pathname.endsWith('/delete')) {
        rows = rows.filter(row => row.id !== JSON.parse(body).id);
        res.end('{"success":true}');
        return;
      }
      const query = url.searchParams.get('search') ?? '';
      const filtered = rows.filter(row => [row.title, row.author].some(value => value.toLowerCase().includes(query.toLowerCase())));
      const page = Number(url.searchParams.get('page') ?? 1);
      const size = Number(url.searchParams.get('page_size') ?? 20);
      const reply = { items: serverMode ? filtered.slice((page - 1) * size, page * size) : rows, stats: { total: filtered.length } };
      if (query.startsWith('stale-') || query === 'abort-old') {
        pending.set(query, () => {
          res.statusCode = query === 'stale-error' ? 500 : 200;
          res.end(JSON.stringify({ items: [rows[0]], stats: { total: 1 } }));
        });
        return;
      }
      if (query === 'error' && rejectOnce) { rejectOnce = false; res.statusCode = 503; res.end('{}'); return; }
      if (query === 'bad-total') reply.stats.total = '61';
      if (query === 'negative-total') reply.stats.total = -1;
      if (query === 'fractional-total') reply.stats.total = 1.5;
      if (query === 'missing-total') delete reply.stats.total;
      if (query === 'bad-rows') reply.items = {};
      if (query === 'short-page') reply.stats.total = 61;
      res.end(JSON.stringify(reply));
      return;
    }
    if (req.url === '/fixture.css') { res.setHeader('Content-Type', 'text/css'); res.end(await styles); return; }
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text : '<html><head><link rel="stylesheet" href="/fixture.css"></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { for (const release of pending.values()) release(); server.closeAllConnections(); return new Promise(resolve => server.close(resolve)); });
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const search = page.getByRole('searchbox', { name: 'Search...' });
  return { page, search, requests, pending };
}

test('server paging fetches beyond 50, searches literal title/author, deletes and clamps, including mobile', async t => {
  const { page, search, requests } = await fixture(t);
  await page.getByText('Page 1 of 4', { exact: true }).waitFor();
  assert.deepEqual(requests.at(-1).query, { page: '1', page_size: '20', search: '' });
  await page.getByRole('cell', { name: 'Book 001', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'Delete', exact: true }).isEnabled(), true);
  for (const number of [2, 3, 4]) {
    await page.getByRole('button', { name: 'Next page', exact: true }).click();
    await page.getByText(`Page ${number} of 4`, { exact: true }).waitFor();
    assert.equal(requests.at(-1).query.page, String(number));
  }
  assert.equal(await page.getByRole('button', { name: 'Delete', exact: true }).isDisabled(), true);
  await page.getByRole('cell', { name: 'Book 061', exact: true }).waitFor();
  await search.fill('Remote Author');
  await page.getByRole('cell', { name: 'Book 056', exact: true }).waitFor();
  await page.getByText('Page 1 of 1', { exact: true }).waitFor();
  assert.equal(requests.at(-1).query.page, '1');
  await search.fill('Literal [x].* &? +');
  await page.getByRole('cell', { name: 'Literal [x].* &? +', exact: true }).waitFor();
  assert.equal(requests.at(-1).query.search, 'Literal [x].* &? +');
  await search.fill('notes-only');
  await page.getByText('0 results', { exact: true }).waitFor();
  await page.getByText('No results', { exact: true }).waitFor();
  await search.fill('');
  await page.getByText('Page 1 of 4', { exact: true }).waitFor();
  const beforeSort = requests.length;
  await page.getByRole('columnheader', { name: 'Title', exact: true }).click();
  assert.equal(await page.getByRole('cell', { name: 'Book 001', exact: true }).isVisible(), true);
  assert.equal(requests.length, beforeSort);
  for (const number of [2, 3, 4]) {
    await page.getByRole('button', { name: 'Next page', exact: true }).click();
    await page.getByText(`Page ${number} of 4`, { exact: true }).waitFor();
  }
  await page.getByRole('cell', { name: 'Book 061', exact: true }).click();
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await page.getByText('Page 3 of 3', { exact: true }).waitFor();
  await page.getByText('60 results', { exact: true }).waitFor();
  assert.deepEqual(requests.slice(-2).map(r => r.query.page), ['4', '3']);
  await page.getByRole('cell', { name: 'Book 041', exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Previous page', exact: true }).click();
  await page.getByText('Page 2 of 3', { exact: true }).waitFor();
  assert.equal(await page.getByText('Book 021', { exact: true }).filter({ visible: true }).count(), 1);
  await search.fill('Remote Author');
  await page.getByText('Page 1 of 1', { exact: true }).waitFor();
  assert.equal(await page.getByText('Book 056', { exact: true }).filter({ visible: true }).count(), 1);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (process.env.MOZAIKS_TEST_SCREENSHOTS) {
    await fs.mkdir(process.env.MOZAIKS_TEST_SCREENSHOTS, { recursive: true });
    await page.screenshot({ path: path.join(process.env.MOZAIKS_TEST_SCREENSHOTS, 'server-pagination-mobile.png') });
  }
  await page.getByRole('radio', { name: 'Select record', exact: true }).check();
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await page.getByText('0 results', { exact: true }).waitFor();
  await page.getByText('Page 1 of 1', { exact: true }).waitFor();
  assert.equal(requests.at(-1).query.search, 'Remote Author');
  assert.equal(await page.getByRole('button', { name: 'Delete', exact: true }).isDisabled(), true);
  assert.ok(requests.every(request => request.auth === 'Bearer fixture'));
});

test('typing a search phrase invalidates selection immediately and fetches only the latest query', async t => {
  const { page, search, requests } = await fixture(t);
  await page.getByText('Page 1 of 4', { exact: true }).waitFor();
  await page.getByRole('cell', { name: 'Book 001', exact: true }).click();
  const count = requests.length;
  await search.pressSequentially('R');
  assert.equal(await search.inputValue(), 'R');
  assert.equal(await page.getByRole('button', { name: 'Delete', exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole('cell', { name: 'Book 001', exact: true }).count(), 0);
  await search.pressSequentially('emote Author', { delay: 15 });
  assert.equal(await search.inputValue(), 'Remote Author');
  await page.getByRole('cell', { name: 'Book 056', exact: true }).waitFor();
  assert.deepEqual(requests.slice(count).map(request => request.query), [
    { page: '1', page_size: '20', search: 'Remote Author' },
  ]);
});

test('invalid totals, row shape, and failures clear loading; stale success/error cannot replace a newer query', async t => {
  const { page, search, pending } = await fixture(t);
  await page.getByText('61 results', { exact: true }).waitFor();
  for (const query of ['error', 'bad-total', 'negative-total', 'fractional-total', 'missing-total', 'bad-rows', 'short-page']) {
    await search.fill(query);
    await page.getByText('Unable to load records', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Retry', exact: true }).isEnabled(), true);
    assert.equal(await page.getByRole('button', { name: 'Next page', exact: true }).count(), 0);
    if (query === 'error') {
      await page.getByRole('button', { name: 'Retry', exact: true }).click();
      await page.getByText('0 results', { exact: true }).waitFor();
      assert.equal(await search.inputValue(), 'error');
    }
  }
  for (const query of ['stale-success', 'stale-error', 'abort-old']) {
    const started = page.waitForRequest(request => new URL(request.url()).searchParams.get('search') === query);
    await search.fill(query);
    await started;
    await search.fill('Remote Author');
    await page.getByRole('cell', { name: 'Book 056', exact: true }).waitFor();
    assert.ok(pending.has(query));
    const delivered = query === 'abort-old' ? null : page.waitForResponse(response => new URL(response.url()).searchParams.get('search') === query);
    pending.get(query)(); pending.delete(query);
    if (delivered) await (await delivered).finished();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.getByText('1 results', { exact: true }).waitFor();
    assert.equal(await page.getByRole('cell', { name: 'Book 001', exact: true }).count(), 0);
    assert.equal(await search.inputValue(), 'Remote Author');
  }
  await search.fill('');
  await page.getByText('61 results', { exact: true }).waitFor();
});

test('client mode still paginates/searches loaded rows without server query parameters', async t => {
  const { page, search, requests } = await fixture(t, { serverMode: false });
  await page.getByText('Page 1 of 4', { exact: true }).waitFor();
  assert.deepEqual(requests.at(-1).query, {});
  const count = requests.length;
  await page.getByRole('button', { name: 'Next page', exact: true }).click();
  await page.getByText('Page 2 of 4', { exact: true }).waitFor();
  await search.fill('Remote Author');
  await page.getByRole('cell', { name: 'Book 056', exact: true }).waitFor();
  assert.equal(requests.length, count);
});

test('ResourceTable server mode fails closed before any module fetch', async t => {
  const { page, requests } = await fixture(t, { primitive: 'ResourceTable' });
  await page.getByText('Unable to load records', { exact: true }).waitFor();
  assert.equal(requests.length, 0);
});
