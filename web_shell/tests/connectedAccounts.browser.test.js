import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

// The profile tab where a user connects their own accounts, and the admin panel
// where the app's admins choose what is offered, against a scripted backend.

const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const { chromium } = require('@playwright/test');
const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const modules = path.dirname(path.dirname(require.resolve('react/package.json')));

const CALENDAR = {
  id: 'google_calendar', module_id: 'google_calendar', provider: 'google', provider_label: 'Google',
  label: 'Google Calendar', description: 'See and add events on your Google Calendar.', scopes: [],
};
const GMAIL = { ...CALENDAR, id: 'gmail_send', module_id: 'gmail', label: 'Gmail', description: 'Send email from your Gmail address.' };

async function harness(t) {
  const stubs = {
    '../../adapters/api.js': `export const authFetch = (input, options = {}) =>
      fetch(input, {...options, headers: {...(options.headers || {}), Authorization: 'Bearer user-token'}});`,
    'react-router-dom': `export const useLocation = () => ({pathname: '/me', search: window.fixture.search});`,
    './AdminPrimitives.jsx': `
      export const Badge = ({children}) => <span data-badge>{children}</span>;
      export const ErrorBox = ({message}) => <div role="alert">{message}</div>;
      export const SectionHeading = ({children}) => <h2>{children}</h2>;`,
  };
  const entry = `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import ConnectedAccountsTab from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/components/profile/ConnectedAccountsTab.jsx'))};
    import AdminConnectedAccountsPanel from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/admin/components/AdminConnectedAccountsPanel.jsx'))};
    window.fixture = {search: '', requests: [], responses: {}, opened: [], confirm: true};
    window.fetch = async (url, options = {}) => {
      const method = options.method || 'GET';
      const key = method + ' ' + new URL(url, location.href).pathname;
      window.fixture.requests.push({key, method, credentials: options.credentials || null,
        authorization: new Headers(options.headers || {}).get('Authorization'),
        body: options.body ? JSON.parse(options.body) : null});
      const queue = window.fixture.responses[key] || [];
      const next = queue.length > 1 ? queue.shift() : queue[0];
      if (!next) return Response.json({detail: 'no fixture for ' + key}, {status: 599});
      return Response.json(next.body, {status: next.status || 200});
    };
    window.open = (url, name, features) => {
      const popup = {closed: false, location: {href: url}, close() { this.closed = true; }};
      if (window.fixture.severOpener) {
        // A provider page with Cross-Origin-Opener-Policy cuts the popup off: it reads as closed.
        let href = url;
        popup.location = {get href() { return href; }, set href(next) { href = next; popup.closed = true; }};
      }
      window.fixture.opened.push({name, features, popup});
      return popup;
    };
    window.confirm = () => window.fixture.confirm;
    const root = createRoot(document.getElementById('root'));
    window.render = which => root.render(which === 'admin' ? <AdminConnectedAccountsPanel /> : <ConnectedAccountsTab />);
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx' },
    nodePaths: [modules], alias: { react: path.join(modules, 'react'), 'react-dom': path.join(modules, 'react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{
      name: 'connected-accounts-fixtures',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => args.path in stubs ? { path: args.path, namespace: 'fixture' } : null);
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({ contents: stubs[args.path], loader: 'jsx' }));
      },
    }],
  });
  const server = http.createServer((req, res) => {
    const script = req.url === '/fixture.js';
    res.setHeader('Content-Type', script ? 'text/javascript' : 'text/html');
    res.end(script ? bundle.outputFiles[0].text : '<html><body><div id="root"></div><script type="module" src="/fixture.js"></script></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(6000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => typeof window.render === 'function');
  return page;
}

async function script(page, responses, search = '') {
  await page.evaluate(({ responses, search }) => {
    window.fixture.responses = responses;
    window.fixture.search = search;
  }, { responses, search });
}

const requests = page => page.evaluate(() => window.fixture.requests);

test('a user connects their own account in a popup the page opened from their click', async t => {
  const page = await harness(t);
  const authorizeUrl = 'https://accounts.google.test/o/oauth2/v2/auth?client_id=c&state=s';
  await script(page, {
    'GET /api/me/connected-accounts': [
      { body: { connections: [{ ...CALENDAR, status: 'not_connected' }, { ...GMAIL, status: 'unavailable' }], providers: [] } },
      { body: {
        connections: [{ ...CALENDAR, status: 'connected' }, { ...GMAIL, status: 'unavailable' }],
        providers: [{ provider: 'google', label: 'Google', account_email: 'alice@gmail.test', status: 'connected' }],
      } },
    ],
    'POST /api/me/connected-accounts/google_calendar/connect': [{ body: { authorize_url: authorizeUrl, flow_id: 'flow-1' } }],
    'GET /api/me/connected-accounts/flows/flow-1': [
      { body: { status: 'pending' } },
      { body: { status: 'connected', message: 'Google Calendar connected.' } },
    ],
  }, '?tab=connected-accounts&connect=google_calendar');
  await page.evaluate(() => window.render('tab'));

  const row = page.locator('[data-connection-id="google_calendar"]');
  await row.getByText('Not connected').waitFor();
  assert.match(await row.getAttribute('class'), /bg-primary\/5/);
  // Unavailable connections explain themselves and offer no button.
  const gmail = page.locator('[data-connection-id="gmail_send"]');
  assert.equal(await gmail.getByRole('button').count(), 0);
  await gmail.getByText('has not finished setting up Google').waitFor();

  await row.getByRole('button', { name: 'Connect' }).click();
  await page.getByRole('status').filter({ hasText: 'Google Calendar connected.' }).waitFor();
  await page.getByText('Connected as alice@gmail.test').waitFor();
  const popup = await page.evaluate(() => {
    const [{ name, popup }] = window.fixture.opened;
    return { name, href: popup.location.href, closed: popup.closed };
  });
  assert.deepEqual(popup, { name: 'mozaiks_account_connect', href: authorizeUrl, closed: true });
  const sent = await requests(page);
  const start = sent.find(request => request.method === 'POST');
  assert.equal(start.credentials, 'include');
  assert.ok(sent.every(request => request.authorization === 'Bearer user-token'));
});

test('the popup is never sent anywhere but an http(s) provider page', async t => {
  const page = await harness(t);
  await script(page, {
    'GET /api/me/connected-accounts': [{ body: { connections: [{ ...CALENDAR, status: 'not_connected' }], providers: [] } }],
    'POST /api/me/connected-accounts/google_calendar/connect': [{ body: { authorize_url: 'javascript:alert(1)', flow_id: 'f' } }],
  });
  await page.evaluate(() => window.render('tab'));
  await page.getByRole('button', { name: 'Connect' }).click();
  await page.getByRole('status').filter({ hasText: 'Could not start connecting Google Calendar' }).waitFor();
  const popup = await page.evaluate(() => window.fixture.opened[0].popup);
  assert.equal(popup.location.href, '');
  assert.equal(popup.closed, true);
});

test('a refused start and a failed flow show the server reason', async t => {
  const page = await harness(t);
  await script(page, {
    'GET /api/me/connected-accounts': [{ body: { connections: [{ ...CALENDAR, status: 'reconnect_required' }], providers: [] } }],
    'POST /api/me/connected-accounts/google_calendar/connect': [
      { status: 503, body: { detail: 'This app does not offer Google Calendar right now.' } },
      { body: { authorize_url: 'https://accounts.google.test/auth', flow_id: 'flow-2' } },
    ],
    'GET /api/me/connected-accounts/flows/flow-2': [
      { body: { status: 'failed', message: 'Google Calendar needs every permission it asked for. Start again and allow them all.' } },
    ],
  });
  await page.evaluate(() => window.render('tab'));
  await page.getByRole('button', { name: 'Reconnect' }).click();
  await page.getByRole('status').filter({ hasText: 'does not offer Google Calendar right now' }).waitFor();
  await page.getByRole('button', { name: 'Reconnect' }).click();
  await page.getByRole('status').filter({ hasText: 'needs every permission' }).waitFor();
  assert.equal(await page.evaluate(() => window.fixture.opened.every(({ popup }) => popup.closed)), true);
});

test('a popup the provider cut off from the page still completes the connection', async t => {
  const page = await harness(t);
  await script(page, {
    'GET /api/me/connected-accounts': [
      { body: { connections: [{ ...CALENDAR, status: 'not_connected' }], providers: [] } },
      { body: { connections: [{ ...CALENDAR, status: 'connected' }], providers: [] } },
    ],
    'POST /api/me/connected-accounts/google_calendar/connect': [{ body: { authorize_url: 'https://accounts.google.test/auth', flow_id: 'flow-3' } }],
    'GET /api/me/connected-accounts/flows/flow-3': [
      { body: { status: 'pending' } }, { body: { status: 'pending' } }, { body: { status: 'pending' } },
      { body: { status: 'connected', message: 'Google Calendar connected.' } },
    ],
  });
  await page.evaluate(() => { window.fixture.severOpener = true; window.render('tab'); });
  await page.getByRole('button', { name: 'Connect' }).click();
  await page.getByRole('status').filter({ hasText: 'Google Calendar connected.' }).waitFor({ timeout: 9000 });
});

test('cancel stops waiting for a flow the user abandoned', async t => {
  const page = await harness(t);
  await script(page, {
    'GET /api/me/connected-accounts': [{ body: { connections: [{ ...CALENDAR, status: 'not_connected' }], providers: [] } }],
    'POST /api/me/connected-accounts/google_calendar/connect': [{ body: { authorize_url: 'https://accounts.google.test/auth', flow_id: 'flow-4' } }],
    'GET /api/me/connected-accounts/flows/flow-4': [{ body: { status: 'pending' } }],
  });
  await page.evaluate(() => window.render('tab'));
  await page.getByRole('button', { name: 'Connect' }).click();
  await page.waitForFunction(() => window.fixture.requests.some(request => request.key.includes('/flows/flow-4')));
  await page.getByRole('button', { name: 'Cancel' }).click();
  await page.getByRole('button', { name: 'Connect' }).waitFor();
  const polls = await page.evaluate(() => window.fixture.requests.filter(request => request.key.includes('/flows/')).length);
  await page.waitForTimeout(2500);
  assert.equal(await page.evaluate(() => window.fixture.requests.filter(request => request.key.includes('/flows/')).length), polls);
  assert.equal(await page.evaluate(() => window.fixture.opened[0].popup.closed), true);
});

test('disconnecting asks first and then revokes the account', async t => {
  const page = await harness(t);
  const connected = {
    connections: [{ ...CALENDAR, status: 'connected' }],
    providers: [{ provider: 'google', label: 'Google', account_email: 'alice@gmail.test', status: 'connected' }],
  };
  await script(page, {
    'GET /api/me/connected-accounts': [{ body: connected }, { body: { connections: [{ ...CALENDAR, status: 'not_connected' }], providers: [] } }],
    'DELETE /api/me/connected-accounts/providers/google': [{ body: { disconnected: true, revoked_at_provider: true } }],
  });
  await page.evaluate(() => { window.fixture.confirm = false; window.render('tab'); });
  await page.getByRole('button', { name: 'Disconnect' }).click();
  assert.equal((await requests(page)).filter(request => request.method === 'DELETE').length, 0);
  await page.evaluate(() => { window.fixture.confirm = true; });
  await page.getByRole('button', { name: 'Disconnect' }).click();
  await page.getByRole('status').filter({ hasText: 'Google disconnected.' }).waitFor();
  await page.getByText('Not connected').waitFor();
  assert.equal(await page.getByText('Connected as').count(), 0);
});

test('the admin panel shows setup, counts and the redirect URI, and toggles the offering', async t => {
  const page = await harness(t);
  const summary = offered => ({
    connections: [{
      ...CALENDAR, offered, client_configured: false,
      client_settings: ['GOOGLE_ACCOUNTS_CLIENT_ID', 'GOOGLE_ACCOUNTS_CLIENT_SECRET'],
    }],
    providers: [{ provider: 'google', connected: 3, needs_reconnect: 1 }],
    redirect_uri: 'https://app.test/api/connected-accounts/callback',
    config_error: null,
  });
  await script(page, {
    'GET /api/admin/connected-accounts': [{ body: summary(true) }, { body: summary(false) }],
    'PUT /api/admin/connected-accounts/google_calendar': [{ body: { id: 'google_calendar', offered: false } }],
  });
  await page.evaluate(() => window.render('admin'));
  await page.getByText('Setup needed').waitFor();
  await page.getByText('GOOGLE_ACCOUNTS_CLIENT_SECRET').waitFor();
  await page.getByText('3 users connected to Google, 1 need to reconnect').waitFor();
  await page.getByText('https://app.test/api/connected-accounts/callback').waitFor();
  await page.getByLabel('Offer to users').uncheck();
  await page.getByText('Not offered').waitFor();
  const put = (await requests(page)).find(request => request.method === 'PUT');
  assert.deepEqual(put.body, { offered: false });
  assert.equal(put.authorization, 'Bearer user-token');
  assert.equal(await page.getByText('alice').count(), 0);
});

for (const [name, response] of [
  ['no plugin declares a connection', { body: { connections: [], providers: [] } }],
  ['the host has no connected accounts', { status: 404, body: { detail: 'Not available' } }],
]) {
  test(`the admin panel stays out of the way when ${name}`, async t => {
    const page = await harness(t);
    await script(page, { 'GET /api/admin/connected-accounts': [response] });
    await page.evaluate(() => window.render('admin'));
    await page.waitForFunction(() => window.fixture.requests.length === 1);
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#root').innerHTML(), '');
  });
}
