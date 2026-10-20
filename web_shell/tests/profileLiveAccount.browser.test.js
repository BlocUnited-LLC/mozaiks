import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { build } = require('esbuild');
const { chromium } = require('@playwright/test');
const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const modules = path.dirname(path.dirname(require.resolve('react/package.json')));

async function harness(t) {
  const stubs = {
    '../context/ChatUIContext': `export const useChatUI = () => window.fixture.context;`,
    '../registry/componentRegistry': `export default {getComponent: () => null};`,
    'react-router-dom': `export const useParams = () => ({username: window.fixture.username});
      export const useLocation = () => ({pathname: window.fixture.username ? '/u/'+window.fixture.username : '/me', search: ''});
      export const useNavigate = () => () => {};`,
  };
  const entry = `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import ProfilePage from ${JSON.stringify(path.resolve(shell, '../chat-ui/src/pages/ProfilePage.jsx'))};
    window.fixture = {username:null, requests:[], context:{
      config:{}, api:{getHttpBaseUrl:()=>''}, user:{id:'alice'},
      auth:{getAccessToken:async()=>'authenticated-account-token'}
    }};
    window.fetch = (url,options={}) => new Promise(resolve => {
      window.fixture.requests.push({url,method:options.method||'GET',headers:options.headers,
        body:options.body ? JSON.parse(options.body) : null,
        // Deliberately ignore abort: late transport completions must be harmless.
        respond:(status,body)=>resolve(Response.json(body,{status}))});
    });
    const root = createRoot(document.getElementById('root'));
    window.renderProfile = () => root.render(<ProfilePage />);
    window.renderProfile();
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, format: 'esm', jsx: 'automatic', loader: { '.js': 'jsx' },
    nodePaths: [modules], alias: { react: path.join(modules, 'react'), 'react-dom': path.join(modules, 'react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{
      name: 'profile-shell-fixtures',
      setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => args.path in stubs ? { path: args.path, namespace: 'fixture' } : null);
        builder.onLoad({ filter: /.*/, namespace: 'fixture' }, args => ({ contents: stubs[args.path], loader: 'js' }));
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
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => window.fixture.requests.length === 3);
  return page;
}

async function respond(page, index, body, status = 200) {
  await page.evaluate(({ index, body, status }) => window.fixture.requests[index].respond(status, body), { index, body, status });
}

async function load(page, start, name, pages = []) {
  await respond(page, start, { user_id: name.toLowerCase(), username: name.toLowerCase(), display_name: name, roles: [] });
  await respond(page, start + 1, { layout: 'top_nav' });
  await respond(page, start + 2, { pages });
}

test('failed owner read is retryable and never invents an identity, role, or saved picture', async t => {
  const page = await harness(t);
  await respond(page, 0, { detail: 'Service unavailable' }, 503);
  await respond(page, 1, { layout: 'top_nav' });
  await respond(page, 2, { pages: [] });
  await page.getByRole('alert').filter({ hasText: 'Could not load this profile' }).waitFor();
  assert.equal(await page.getByText('Demo User').count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Edit profile' }).count(), 0);
  await page.evaluate(() => localStorage.setItem('mozaiks_profile_avatar_alice', 'data:image/png;base64,AAAA'));
  await page.getByRole('button', { name: 'Try again' }).click();
  await page.waitForFunction(() => window.fixture.requests.length === 6);
  await load(page, 3, 'Alice');
  await page.getByRole('heading', { name: 'Alice' }).waitFor();
  assert.equal(await page.locator('input[type=file]').count(), 0);
  assert.equal(await page.locator('img').count(), 0);
  assert.equal(await page.getByText('admin', { exact: true }).count(), 0);
  const requests = await page.evaluate(() => window.fixture.requests.map(({ url, headers }) => ({ url, headers })));
  assert.ok(requests.every(request => request.url.startsWith(page.url())));
  assert.ok(requests.every(request => request.headers.Authorization === 'Bearer authenticated-account-token'));
});

test('late profile and section responses cannot cross the viewed person or signed-in account', async t => {
  const page = await harness(t);
  await page.evaluate(() => { window.fixture.username = 'bob'; window.renderProfile(); });
  await page.waitForFunction(() => window.fixture.requests.length === 6);
  await load(page, 3, 'Bob', [
    { id: 'public', label: 'Bob public', visibility: 'public', data: { bio: 'Bob data' } },
    { id: 'private', label: 'Private owner tab', visibility: 'owner_only', data: { secret: 'private' } },
  ]);
  await page.getByRole('heading', { name: 'Bob' }).waitFor();
  await load(page, 0, 'Alice', [{ id: 'old', label: 'Alice private', visibility: 'owner_only' }]);
  assert.equal(await page.getByRole('heading', { name: 'Alice' }).count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Private owner tab' }).count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Alice private' }).count(), 0);
  await page.evaluate(() => {
    window.fixture.username = null; window.fixture.context.user = { id: 'charlie' }; window.renderProfile();
  });
  await page.waitForFunction(() => window.fixture.requests.length === 9);
  assert.equal(await page.getByRole('heading', { name: 'Bob' }).count(), 0);
  await load(page, 6, 'Charlie');
  await page.getByRole('heading', { name: 'Charlie' }).waitFor();
  await page.getByRole('button', { name: 'Edit profile' }).waitFor();
});

test('failed edits keep the draft, duplicate saves are fenced and account changes abandon pending edits', async t => {
  const page = await harness(t);
  await load(page, 0, 'Alice');
  await page.getByRole('button', { name: 'Edit profile' }).click();
  await page.getByPlaceholder('Display name').fill('Alice Updated');
  await page.getByRole('button', { name: 'Save', exact: true }).evaluate(button => { button.click(); button.click(); });
  await page.waitForFunction(() => window.fixture.requests.length === 4);
  assert.equal(await page.evaluate(() => window.fixture.requests.filter(r => r.method === 'PUT').length), 1);
  await respond(page, 3, {}, 503);
  await page.getByText('503').waitFor();
  assert.equal(await page.getByPlaceholder('Display name').inputValue(), 'Alice Updated');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.waitForFunction(() => window.fixture.requests.length === 5);
  await page.evaluate(() => { window.fixture.context.user = { id: 'bob' }; window.renderProfile(); });
  await page.waitForFunction(() => window.fixture.requests.length === 8);
  await load(page, 5, 'Bob');
  await page.getByRole('heading', { name: 'Bob' }).waitFor();
  await respond(page, 4, {});
  assert.equal(await page.getByPlaceholder('Display name').count(), 0);
  assert.equal(await page.evaluate(() => window.fixture.requests.length), 8);
});

test('section API failures show a retry rather than silently looking empty', async t => {
  const page = await harness(t);
  await respond(page, 0, { username: 'alice', display_name: 'Alice' });
  await respond(page, 1, { layout: 'top_nav' });
  await respond(page, 2, {}, 503);
  await page.getByRole('alert').filter({ hasText: 'Could not load profile sections' }).waitFor();
  await page.getByRole('button', { name: 'Try again' }).click();
  await page.waitForFunction(() => window.fixture.requests.length === 6);
  await load(page, 3, 'Alice', [{ id: 'activity', label: 'Activity', data: { count: 1 } }]);
  await page.getByRole('button', { name: 'Activity', exact: true }).waitFor();
});

test('malformed successful responses stay retryable instead of inventing a profile or crashing sections', async t => {
  const page = await harness(t);
  const malformedSections = [
    { sections: { overview: {} } },
    { sections: { overview: [null] } },
    { pages: [{ label: 'Missing identity' }] },
    {},
  ];
  for (let index = 0; index < malformedSections.length; index += 1) {
    const start = index * 3;
    await respond(page, start, { user_id: 'alice', username: 'alice', display_name: 'Alice' });
    await respond(page, start + 1, { layout: 'top_nav' });
    await respond(page, start + 2, malformedSections[index]);
    await page.getByRole('alert').filter({ hasText: 'Could not load profile sections' }).waitFor();
    await page.getByRole('button', { name: 'Try again' }).click();
    await page.waitForFunction(count => window.fixture.requests.length === count, start + 6);
  }
  const start = malformedSections.length * 3;
  await respond(page, start, {});
  await respond(page, start + 1, { layout: 'top_nav' });
  await respond(page, start + 2, { pages: [] });
  await page.getByRole('alert').filter({ hasText: 'Could not load this profile' }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Edit profile' }).count(), 0);
  assert.equal(await page.getByText('Demo User').count(), 0);
});
