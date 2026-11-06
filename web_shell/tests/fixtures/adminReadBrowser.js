import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';

const readPath = '/api/admin/modules/catalog/list_inventory/read';
const row = name => ({ items: [{ name, status: 'ready' }] });
const tableSection = endpoint => ({ id: 'catalog-table', primitive: 'DataTable', config: {
  api_endpoint: endpoint, data_key: 'items', columns: [{ key: 'name', label: 'Name' }], search: false,
} });

async function waitForRequest(predicate) {
  const deadline = Date.now() + 8000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, 'Expected browser request did not arrive');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}

async function fixture(t, { root, shell, styles }) {
  // Generated from the real registry, shell route builder and module loader;
  // tests/test_admin_read_browser_fixture.py guards the serialized contract.
  const registry = JSON.parse(await fs.readFile(path.join(shell, 'tests/fixtures/adminRead.json'), 'utf8'));
  const requests = [];
  const replies = new Map();
  const pending = new Set();
  function enqueue(endpoint, body, { status = 200, held = false } = {}) {
    let release;
    const ready = held ? new Promise(resolve => { release = resolve; }) : Promise.resolve();
    if (release) pending.add(release);
    const queue = replies.get(endpoint) ?? [];
    queue.push({ body, status, ready });
    replies.set(endpoint, queue);
    return () => { release?.(); pending.delete(release); };
  }
  const entry = `
    import {useState} from 'react'; import {createRoot} from 'react-dom/client';
    import {BrowserRouter, useNavigate} from 'react-router-dom';
    import AdminPage from ${JSON.stringify(path.join(root, 'chat-ui/src/pages/AdminPage.jsx'))};
    import {useAdminFetch} from ${JSON.stringify(path.join(root, 'chat-ui/src/admin/components/AdminPrimitives.jsx'))};
    import {PageRenderer} from ${JSON.stringify(path.join(root, 'chat-ui/src/ui/page-renderer/index.js'))};
    import {emitAppEvent} from ${JSON.stringify(path.join(root, 'chat-ui/src/ui/hooks/useAppEventBus.js'))};
    window.__token = 'admin-test-session'; window.__roles = ['admin']; window.__aborts = 0;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (url, options = {}) => {
      options.signal?.addEventListener('abort', () => { window.__aborts += 1; }, {once: true});
      return nativeFetch(url, window.__ignoreAbort ? {...options, signal: undefined} : options);
    };
    function Probe({endpoint}) {
      const {data, loading, error, refresh} = useAdminFetch(endpoint);
      return <section><button onClick={refresh}>Refresh probe</button><output data-testid="probe">{JSON.stringify({data, loading, error})}</output></section>;
    }
    function Fixture() {
      const [view, setView] = useState({kind: 'admin', route: ${JSON.stringify(registry.route)}});
      const navigate = useNavigate();
      window.showAdmin = route => {navigate(route.path); setView({kind: 'admin', route});};
      window.showPage = sections => setView({kind: 'page', schema: {name: 'inventory', title: 'Inventory', layout: 'full-width', sections}});
      window.showProbe = endpoint => setView({kind: 'probe', endpoint});
      window.hideSurface = () => setView({kind: 'none'});
      window.refreshTable = () => emitAppEvent('ui.datatable.refresh', {component_id: 'catalog-table'});
      return view.kind === 'admin' ? <AdminPage route={view.route} />
        : view.kind === 'page' ? <PageRenderer schema={view.schema} />
        : view.kind === 'probe' ? <Probe endpoint={view.endpoint} /> : <p>Surface closed</p>;
    }
    createRoot(document.getElementById('root')).render(<BrowserRouter><Fixture /></BrowserRouter>);
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' }, bundle: true, write: false,
    jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl' }, nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'admin-environment-only', setup(bundler) {
      bundler.onResolve({ filter: /./ }, args => {
        let contents;
        if (/context\/ChatUIContext$/.test(args.path)) contents = 'export const useChatUI = () => ({user: {roles: window.__roles}, auth: {}});';
        else if (/AdminWorkspaceLayout\.jsx$/.test(args.path)) contents = 'export const AdminWorkspaceLayout = ({children}) => children;';
        else if (/admin\/pages\//.test(args.path)) {
          contents = ['OverviewSection', 'UsersSection', 'UsageSection', 'OperationsSection', 'SettingsSection']
            .map(name => 'export const ' + name + ' = () => <div>' + name + '</div>;').join('\n');
        } else if (/registry\/componentRegistry$/.test(args.path)) contents = 'export const getComponent = () => null;';
        else if (/hooks\/useWorkflowStart\.js$/.test(args.path)) contents = 'export const useWorkflowStart = () => ({startWorkflow: async () => {}});';
        else if (/adapters[/\\]api\.js$/.test(args.importer)) {
          if (args.path === '../config') contents = 'export default {get: key => key === "api.baseUrl" ? "/gateway" : undefined};';
          else if (args.path === '../platform/index.js') contents = 'export default {resolveHttpUrl: () => "/gateway", getAccessToken: () => window.__token};';
          else if (args.path === '../config/workflowConfig') contents = 'export default {};';
          else if (args.path === '../utils/resolveWorkflow') contents = 'export default value => value;';
        }
        if (contents !== undefined) return { path: args.path, namespace: 'admin-environment', pluginData: { contents } };
      });
      bundler.onLoad({ filter: /.*/, namespace: 'admin-environment' }, args => ({ contents: args.pluginData.contents, loader: 'jsx' }));
    } }],
  });
  const server = http.createServer(async (req, res) => {
    if (req.url.startsWith('/gateway/api/')) {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const endpoint = req.url.slice('/gateway'.length);
      requests.push({ endpoint, method: req.method, authorization: req.headers.authorization, contentType: req.headers['content-type'], body: raw ? JSON.parse(raw) : null });
      const reply = replies.get(endpoint)?.shift() ?? {
        status: 200, body: endpoint === '/api/admin/config' ? registry.config : row('Default entry'), ready: Promise.resolve(),
      };
      await reply.ready;
      if (res.destroyed) return;
      res.statusCode = reply.status;
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify(reply.body));
      return;
    }
    if (req.url === '/fixture.css') { res.setHeader('Content-Type', 'text/css'); res.end(await styles); return; }
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text : '<html><head><link rel="stylesheet" href="/fixture.css"></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { for (const release of pending) release(); server.closeAllConnections(); return new Promise(resolve => server.close(resolve)); });
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(8000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  return { page, requests, enqueue, errors, registry, url: `http://127.0.0.1:${server.address().port}${registry.route.path}` };
}

export function registerAdminReadTests(test, options) {
  test('registry admin page preserves its ID and authenticates config, reads, retry and refresh', async t => {
    const f = await fixture(t, options);
    f.enqueue('/api/admin/config', {}, { status: 401 });
    f.enqueue(readPath, {}, { status: 403 });
    f.enqueue(readPath, row('First entry'));
    await f.page.goto(f.url);
    await f.page.getByText('Unable to load admin configuration', { exact: true }).waitFor();
    assert.equal(f.requests.filter(request => request.endpoint === readPath).length, 0);
    await f.page.getByRole('button', { name: 'Retry', exact: true }).click();
    await f.page.getByRole('heading', { name: f.registry.config.pages[0].label, exact: true }).waitFor();
    await f.page.getByText('Unable to load records', { exact: true }).waitFor();
    assert.equal(await f.page.getByText('No results', { exact: true }).count(), 0);
    await f.page.getByRole('button', { name: 'Retry', exact: true }).click();
    await f.page.getByRole('cell', { name: 'First entry', exact: true }).waitFor();
    f.enqueue(readPath, row('Refreshed entry'));
    await f.page.evaluate(() => window.refreshTable());
    try { await f.page.getByRole('cell', { name: 'Refreshed entry', exact: true }).waitFor(); }
    catch (error) { t.diagnostic(JSON.stringify({ errors: f.errors, requests: f.requests, snapshot: await f.page.locator('body').ariaSnapshot() })); throw error; }
    f.enqueue(readPath, { items: [] });
    await f.page.evaluate(() => window.refreshTable());
    await f.page.getByText('No results', { exact: true }).waitFor();
    f.enqueue(readPath, {}, { status: 503 });
    await f.page.evaluate(() => window.refreshTable());
    await f.page.getByText('Unable to load records', { exact: true }).waitFor();
    assert.equal(await f.page.getByText('No results', { exact: true }).count(), 0);
    assert.ok(f.requests.every(request => request.authorization === 'Bearer admin-test-session'));
    assert.ok(f.requests.filter(request => request.endpoint === '/api/admin/config').every(request => request.method === 'GET'));
    const reads = f.requests.filter(request => request.endpoint === readPath);
    assert.equal(reads.length, 5);
    assert.ok(reads.every(request => request.method === 'POST' && JSON.stringify(request.body) === '{}' && request.contentType === 'application/json'));
    assert.equal(f.requests.filter(request => request.endpoint === '/api/admin/modules/catalog/list_inventory').length, 0);
    // Preserve the existing app-dashboard suffix route and role guard.
    await f.page.evaluate(() => window.showAdmin({ path: '/apps/app-1/access' }));
    await f.page.getByText('UsersSection', { exact: true }).waitFor();
    await f.page.evaluate(() => { window.__roles = ['member']; window.showAdmin({ path: '/apps/app-1/access' }); });
    await f.page.getByRole('heading', { name: 'Access Denied' }).waitFor();
    assert.deepEqual(f.errors, []);
  });

  test('rendered admin reads and config ignore stale responses and cancel on unmount', async t => {
    const f = await fixture(t, options);
    await f.page.goto(f.url);
    await f.page.getByRole('cell', { name: 'Default entry', exact: true }).waitFor();
    // The transport deliberately ignores abort so request identity is tested too.
    await f.page.evaluate(() => { window.__ignoreAbort = true; });
    const oldEndpoint = '/api/admin/modules/catalog/old_inventory/read';
    const newEndpoint = '/api/admin/modules/catalog/new_inventory/read';
    const releaseOld = f.enqueue(oldEndpoint, row('Stale entry'), { held: true });
    await f.page.evaluate(section => window.showPage([section]), tableSection(oldEndpoint));
    await f.page.waitForFunction(() => document.querySelector('[data-page="inventory"]') || document.body.textContent.includes('Inventory'));
    await waitForRequest(() => f.requests.some(request => request.endpoint === oldEndpoint));
    f.enqueue(newEndpoint, row('Newest entry'));
    await f.page.evaluate(section => window.showPage([section]), tableSection(newEndpoint));
    await f.page.getByRole('cell', { name: 'Newest entry', exact: true }).waitFor();
    releaseOld();
    await f.page.waitForLoadState('networkidle');
    assert.equal(await f.page.getByRole('cell', { name: 'Stale entry', exact: true }).count(), 0);
    const releaseUnmounted = f.enqueue(newEndpoint, row('Unmounted entry'), { held: true });
    const count = f.requests.filter(request => request.endpoint === newEndpoint).length;
    await f.page.evaluate(() => window.refreshTable());
    await waitForRequest(() => f.requests.filter(request => request.endpoint === newEndpoint).length > count);
    await f.page.evaluate(() => window.hideSurface());
    releaseUnmounted();
    await f.page.waitForLoadState('networkidle');
    await f.page.getByText('Surface closed', { exact: true }).waitFor();
    const releaseConfig = f.enqueue('/api/admin/slow-config', { value: 'stale' }, { held: true });
    await f.page.evaluate(() => window.showProbe('/api/admin/slow-config'));
    await waitForRequest(() => f.requests.some(request => request.endpoint === '/api/admin/slow-config'));
    f.enqueue('/api/admin/new-config', { value: 'current' });
    await f.page.evaluate(() => window.showProbe('/api/admin/new-config'));
    await f.page.getByTestId('probe').filter({ hasText: 'current' }).waitFor();
    releaseConfig();
    await f.page.waitForLoadState('networkidle');
    assert.deepEqual(JSON.parse(await f.page.getByTestId('probe').textContent()), { data: { value: 'current' }, loading: false, error: null });
    f.enqueue('/api/admin/new-config', {}, { status: 503 });
    await f.page.getByRole('button', { name: 'Refresh probe' }).click();
    await f.page.getByTestId('probe').filter({ hasText: '503' }).waitFor();
    assert.equal(JSON.parse(await f.page.getByTestId('probe').textContent()).data, null);
    const releaseClosedConfig = f.enqueue('/api/admin/new-config', { value: 'closed' }, { held: true });
    const configCount = f.requests.filter(request => request.endpoint === '/api/admin/new-config').length;
    await f.page.getByRole('button', { name: 'Refresh probe' }).click();
    await waitForRequest(() => f.requests.filter(request => request.endpoint === '/api/admin/new-config').length > configCount);
    await f.page.evaluate(() => window.hideSurface());
    releaseClosedConfig();
    await f.page.waitForLoadState('networkidle');
    await f.page.getByText('Surface closed', { exact: true }).waitFor();
    assert.ok(await f.page.evaluate(() => window.__aborts >= 3));
    assert.ok(f.requests.every(request => request.authorization === 'Bearer admin-test-session'));
    assert.deepEqual(f.errors, []);
  });

  test('automatic reads preserve public GET and never POST an ordinary admin action', async t => {
    const f = await fixture(t, options);
    await f.page.goto(f.url);
    await f.page.getByRole('cell', { name: 'Default entry', exact: true }).waitFor();
    const ordinaryAction = '/api/admin/modules/catalog/add_entry';
    const publicRead = '/api/modules/catalog/list_inventory';
    f.enqueue(ordinaryAction, {}, { status: 405 });
    f.enqueue(publicRead, row('Public entry'));
    await f.page.evaluate(sections => window.showPage(sections), [
      { ...tableSection(ordinaryAction), id: 'unadmitted-table' },
      { ...tableSection(publicRead), id: 'public-table' },
      { id: 'explicit-action', primitive: 'Button', config: { label: 'Add entry', action: { label: 'Add entry', action_type: 'submit', href: ordinaryAction, payload: { name: 'Requested entry' } } } },
    ]);
    await f.page.getByRole('cell', { name: 'Public entry', exact: true }).waitFor();
    await f.page.getByText('Unable to load records', { exact: true }).waitFor();
    assert.equal(f.requests.find(request => request.endpoint === publicRead).method, 'GET');
    assert.equal(f.requests.find(request => request.endpoint === ordinaryAction).method, 'GET');
    assert.equal(f.requests.filter(request => request.endpoint === ordinaryAction && request.method === 'POST').length, 0);
    await f.page.getByRole('button', { name: 'Add entry', exact: true }).click();
    await f.page.waitForLoadState('networkidle');
    const write = f.requests.find(request => request.endpoint === ordinaryAction && request.method === 'POST');
    assert.deepEqual(write.body, { name: 'Requested entry' });
    assert.equal(write.authorization, 'Bearer admin-test-session');
    assert.deepEqual(f.errors, []);
  });

  test('explicit admin deletes use module POST while ordinary REST deletes stay DELETE', async t => {
    const f = await fixture(t, options);
    await f.page.goto(f.url);
    await f.page.getByRole('cell', { name: 'Default entry', exact: true }).waitFor();
    const adminDelete = '/api/admin/modules/catalog/delete_entry';
    assert.equal(f.requests.filter(request => request.endpoint === adminDelete).length, 0);
    // This action comes from the actual compiler/registry fixture, not a hand-built panel.
    await f.page.getByRole('button', { name: 'Delete entry', exact: true }).click();
    await waitForRequest(() => f.requests.some(request => request.endpoint === adminDelete));
    const adminRequest = f.requests.find(request => request.endpoint === adminDelete);
    assert.equal(adminRequest.method, 'POST');
    assert.deepEqual(adminRequest.body, {});
    const publicDelete = '/api/modules/catalog/delete_entry';
    const restDelete = '/api/records/item-1';
    await f.page.evaluate(targets => window.showPage(targets.map(([label, href]) => ({
      id: label, primitive: 'Button', config: { label, action: {
        label, action_type: 'delete', href, payload: { record_id: 'item-1' },
      } },
    }))), [['Delete public entry', publicDelete], ['Delete REST record', restDelete]]);
    for (const [label, endpoint, method] of [
      ['Delete public entry', publicDelete, 'POST'], ['Delete REST record', restDelete, 'DELETE'],
    ]) {
      assert.equal(f.requests.filter(request => request.endpoint === endpoint).length, 0);
      await f.page.getByRole('button', { name: label, exact: true }).click();
      await waitForRequest(() => f.requests.some(request => request.endpoint === endpoint));
      const request = f.requests.find(request => request.endpoint === endpoint);
      assert.equal(request.method, method);
      assert.deepEqual(request.body, { record_id: 'item-1' });
    }
    assert.ok(f.requests.every(request => request.authorization === 'Bearer admin-test-session'));
    assert.deepEqual(f.errors, []);
  });
}
