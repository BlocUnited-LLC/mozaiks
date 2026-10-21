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
const chatUi = path.resolve(shell, '../chat-ui/src');
const brand = (name, primary = 'orange', color = '#c2410c') => ({
  identity: { name },
  assets: { logo: '/assets/app-logo.svg' },
  theme: { primary, appearance: 'light', font: 'system', font_heading: 'system', font_logo: 'system' },
  fonts: { body: { family: 'Georgia', fallbacks: 'serif' } },
  colors: { primary: { main: color }, background: { base: '#fff7ed' } },
});

test('theme bootstrap paints the app only after its own brand is ready', async t => {
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import App from './App.jsx';
      import { Switcher } from '@mozaiks/chat-ui';
      window.renderedThemes = [];
      createRoot(document.getElementById('root')).render(<React.StrictMode>
        {location.search.includes('switch') ? <Switcher /> : <App />}
      </React.StrictMode>);
    ` },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
    define: { 'import.meta.env': '{}', 'process.env.NODE_ENV': '"test"' },
    alias: {
      react: path.join(shell, 'node_modules/react'),
      'react-dom': path.join(shell, 'node_modules/react-dom'),
    },
    nodePaths: [path.join(shell, 'node_modules')],
    plugins: [{ name: 'auth-and-app-fixture', setup(builder) {
      builder.onResolve({ filter: /^@mozaiks\/chat-ui$/ }, () => ({ path: 'chat-ui', namespace: 'fixture' }));
      builder.onResolve({ filter: /^@platform\/extensions$/ }, () => ({ path: 'extensions', namespace: 'fixture' }));
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path: kind }) => ({
        resolveDir: shell, loader: 'jsx', contents: kind === 'extensions' ? 'export {};' : `
          import React, { useState } from 'react';
          import * as themeProvider from ${JSON.stringify(path.join(chatUi, 'styles/themeProvider.js'))};
          import { useTheme } from ${JSON.stringify(path.join(chatUi, 'styles/useTheme.js'))};
          import { ChatUIProvider } from ${JSON.stringify(path.join(chatUi, 'context/ChatUIContext.jsx'))};
          export { themeProvider };
          export const componentRegistry = { hasComponent: () => true };
          export class WebSocketApiAdapter {}
          export const LoginPage = () => null;
          export const AuthCallbackPage = () => null;
          const userResolvers = [];
          const authAdapter = {
            getAccessToken: () => 'fixture-token',
            getCurrentUser: () => new Promise(resolve => {
              userResolvers.push(resolve);
              window.finishUser = () => userResolvers.forEach(done => done({ id: 'owner' }));
            }),
          };
          export function loadShellAuth() {
            window.authCalls = (window.authCalls || 0) + 1;
            return new Promise((resolve, reject) => {
              window.finishAuth = () => resolve({ authAdapter, shellConfig: { appId: 'bakery' } });
              window.failAuth = () => reject(new Error('fixture auth unavailable'));
            });
          }
          function Probe({ appId }) {
            const { theme, loading } = useTheme(appId);
            window.renderedThemes.push({ appId, name: theme.branding.name, loading,
              primary: document.documentElement.style.getPropertyValue('--color-primary'),
              appPrimary: document.documentElement.style.getPropertyValue('--mz-primary') });
            return <section data-testid="theme-probe" data-loading={loading}>
              <h1>{theme.branding.name}</h1><img src={theme.branding.logo} alt="App logo" />
            </section>;
          }
          export function MozaiksApp({ authAdapter, shellConfig }) {
            window.shellMounted = true;
            return <ChatUIProvider authAdapter={authAdapter} uiConfig={{chat:{defaultAppId:shellConfig.appId}}}>
              <Probe appId={shellConfig.appId} />
            </ChatUIProvider>;
          }
          export function Switcher() {
            const [appId, setAppId] = useState('slow-app');
            window.switchApp = setAppId;
            window.themeProvider = themeProvider;
            return <Probe appId={appId} />;
          }
        `,
      }));
    } }],
  });
  const styles = await postcss([tailwindcss()]).process(
    await fs.readFile(path.join(shell, 'styles.css'), 'utf8')
      + `\n@source "${chatUi.replaceAll('\\', '/')}";`,
    { from: path.join(shell, 'styles.css') },
  );
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text
      : `<!doctype html><style>${styles.css}</style><div id="root"></div><script type="module" src="/fixture.js"></script>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const baseUrl = `http://127.0.0.1:${server.address().port}`;

  async function pageFixture(subtest, { holdTheme = false, switchThemes = false } = {}) {
    const page = await browser.newPage();
    subtest.after(() => page.close());
    const errors = [];
    const requests = [];
    const pending = [];
    const done = [];
    let baseRequests = 0;
    page.on('pageerror', error => errors.push(error.message));
    subtest.after(() => assert.deepEqual(errors, []));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin === baseUrl && (route.request().isNavigationRequest() || url.pathname === '/fixture.js')) {
        return route.continue();
      }
      requests.push(url.pathname);
      if (url.pathname === '/api/theme-config') {
        const number = ++baseRequests;
        if (holdTheme && number === 1) await new Promise(resolve => pending.push(resolve));
        const result = switchThemes
          ? brand(number === 1 ? 'Slow app' : 'Current app', number === 1 ? 'orange' : 'teal', number === 1 ? '#c2410c' : '#0f766e')
          : brand('Bakery');
        await route.fulfill({ json: result });
        done.push(number);
        return;
      }
      if (url.pathname.startsWith('/api/themes/')) {
        assert.equal(route.request().headers().authorization, switchThemes ? undefined : 'Bearer fixture-token');
        return route.fulfill({ json: {} });
      }
      if (url.pathname === '/assets/app-logo.svg') return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" />' });
      if (url.pathname.startsWith('/fonts/')) return route.fulfill({ status: 404, body: '' });
      assert.fail(`Unexpected browser request: ${url}`);
    });
    return { page, requests, pending, done };
  }

  await t.test('auth then delayed theme then user initialization preserve the app brand', async st => {
    const { page, requests, pending } = await pageFixture(st, { holdTheme: true });
    await page.goto(baseUrl);
    await expect(page.getByRole('status')).toHaveText('Loading app…');
    assert.equal(requests.length, 0);
    await page.evaluate(() => window.finishAuth());
    await expect.poll(() => pending.length).toBe(1);
    assert.equal(await page.evaluate(() => !!window.shellMounted), false);
    await expect(page.getByRole('heading')).toHaveCount(0);
    pending.shift()();
    await expect.poll(() => page.evaluate(() => typeof window.finishUser)).toBe('function');
    await expect(page.getByRole('status')).toHaveText('Loading app…');
    await expect(page.getByRole('status').locator('../..')).toHaveCSS('background-color', 'rgb(255, 247, 237)');
    await expect(page.getByRole('status').locator('../..')).toHaveCSS('background-image', 'none');
    await page.evaluate(() => window.finishUser());
    await expect(page.getByRole('heading')).toHaveText('Bakery');
    const renders = await page.evaluate(() => window.renderedThemes);
    assert.ok(renders.length > 0);
    assert.ok(renders.every(value => value.name === 'Bakery' && !value.loading && value.primary === '#c2410c' && value.appPrimary));
    assert.equal(requests.filter(url => url === '/api/theme-config').length, 1);
    assert.equal(requests.filter(url => url === '/api/themes/bakery').length, 1);
    assert.equal(await page.evaluate(() => window.authCalls), 1);
  });

  await t.test('auth failure keeps the existing retry boundary ahead of theme loading', async st => {
    const { page, requests } = await pageFixture(st);
    await page.goto(baseUrl);
    await expect(page.getByRole('status')).toBeVisible();
    await page.evaluate(() => window.failAuth());
    await expect(page.getByRole('alert')).toContainText('sign-in settings');
    assert.deepEqual(requests, []);
    await page.getByRole('button', { name: 'Try again' }).click();
    await expect.poll(() => page.evaluate(() => window.authCalls)).toBe(2);
    await page.evaluate(() => window.finishAuth());
    await expect.poll(() => page.evaluate(() => typeof window.finishUser)).toBe('function');
    await page.evaluate(() => window.finishUser());
    await expect(page.getByRole('heading')).toHaveText('Bakery');
  });

  await t.test('late hooks and initializers cannot repaint the active app when storage is disabled', async st => {
    const { page, pending, done, requests } = await pageFixture(st, { holdTheme: true, switchThemes: true });
    await page.goto(`${baseUrl}?switch`);
    await expect.poll(() => pending.length).toBe(1);
    await page.evaluate(() => {
      Storage.prototype.setItem = () => { throw new Error('storage disabled'); };
      window.slowInitialization = window.themeProvider.initializeTheme('slow-app');
      window.currentInitialization = window.themeProvider.initializeTheme('current-app');
      window.switchApp('current-app');
    });
    await expect(page.getByRole('heading')).toHaveText('Current app');
    const primary = await page.evaluate(() => document.documentElement.style.getPropertyValue('--mz-primary'));
    pending.shift()();
    await expect.poll(() => done.includes(1)).toBe(true);
    await page.evaluate(() => Promise.all([window.slowInitialization, window.currentInitialization]));
    await expect.poll(() => page.evaluate(() => window.themeProvider.getCachedTheme('slow-app')?.branding.name)).toBe('Slow app');
    await expect(page.getByRole('heading')).toHaveText('Current app');
    assert.equal(await page.evaluate(() => document.documentElement.style.getPropertyValue('--color-primary')), '#0f766e');
    assert.equal(await page.evaluate(() => document.documentElement.style.getPropertyValue('--mz-primary')), primary);
    assert.equal(requests.filter(url => url === '/api/theme-config').length, 2);
    await page.evaluate(() => { window.renderedThemes = []; window.switchApp('slow-app'); });
    await expect(page.getByRole('heading')).toHaveText('Slow app');
    assert.ok((await page.evaluate(() => window.renderedThemes)).every(value => value.name === 'Slow app' && !value.loading));
    assert.equal(requests.filter(url => url === '/api/theme-config').length, 2);
  });
});
