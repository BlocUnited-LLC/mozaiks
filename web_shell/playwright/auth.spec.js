import { expect, test } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { parse } from 'yaml';

const issuer = 'https://identity.example';
const appOrigin = 'http://127.0.0.1:4187';
const factoryContract = parse(readFileSync(new URL('../../factory_app/app/config/auth.yaml', import.meta.url), 'utf8'));
const factoryRoutes = JSON.parse(readFileSync(new URL('../../factory_app/app/ui/route_manifest.json', import.meta.url), 'utf8')).pages;
const factoryTheme = JSON.parse(readFileSync(new URL('../../factory_app/app/brand/theme_config.json', import.meta.url), 'utf8'));
function shellConfig() {
  return {
    appName: 'Factory Auth Acceptance', appId: 'factory-auth-acceptance', landing_spot: '/apps',
    pages: factoryRoutes.filter(route => ['/login', '/auth/callback', '/apps'].includes(route.path)),
    auth: {
      required: true,
      contract: factoryContract,
      runtime: { enabled: true, provider: 'jwt', local_development: false, user: null },
      frontend: { authority: issuer, discovery_url: '', client_id: 'factory-browser', redirect_uri: '', scope: 'openid profile email' },
    },
  };
}

async function mockBackend(page, config = shellConfig()) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/shell-config') return route.fulfill({ json: config });
    if (path === '/api/theme-config') return route.fulfill({ json: factoryTheme });
    return route.fulfill({ json: { apps: [], items: [], pages: [], workflows: [], data: [] } });
  });
}

// Studio's resolved profile menu: shell.json profile shortcuts plus the injected Admin Portal entry.
const studioProfileMenu = [
  { id: 'profile', label: 'Profile', action: 'navigate', path: '/me' },
  { id: 'admin-portal', label: 'Admin Portal', action: 'navigate', path: '/apps', requiresRole: 'admin' },
  { id: 'signout', label: 'Sign Out', action: 'signout' },
];

function localModeShellConfig() {
  const config = shellConfig();
  config.auth.frontend = null;
  config.auth.runtime = { enabled: false, provider: 'none', local_development: true, user: { id: 'configured-local-user', name: 'Local User', roles: ['admin', 'user'] } };
  return config;
}

async function mockIdentityProvider(page) {
  let authorization;
  let exchanges = 0;
  await page.route(`${issuer}/**`, async route => {
    const url = new URL(route.request().url());
    const headers = { 'access-control-allow-origin': appOrigin, 'access-control-allow-headers': 'content-type' };
    if (url.pathname === '/.well-known/openid-configuration') return route.fulfill({
      headers, json: { issuer, authorization_endpoint: `${issuer}/authorize`, token_endpoint: `${issuer}/token` },
    });
    if (url.pathname === '/authorize') {
      authorization = url.searchParams;
      const callback = new URL(authorization.get('redirect_uri'));
      callback.searchParams.set('code', 'local-test-code');
      callback.searchParams.set('state', authorization.get('state'));
      return route.fulfill({ status: 302, headers: { location: callback.href } });
    }
    if (url.pathname === '/token') {
      exchanges += 1;
      const body = new URLSearchParams(route.request().postData());
      expect(body.get('redirect_uri')).toBe(`${appOrigin}/auth/callback`);
      expect(body.get('code')).toBe('local-test-code');
      expect(createHash('sha256').update(body.get('code_verifier')).digest('base64url')).toBe(authorization.get('code_challenge'));
      const now = Math.floor(Date.now() / 1000);
      const claims = { iss: issuer, aud: 'factory-browser', sub: 'browser-user', nonce: authorization.get('nonce'), exp: now + 1800, iat: now, name: 'Browser User', roles: ['admin', 'user'] };
      const token = `${Buffer.from('{"alg":"RS256"}').toString('base64url')}.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.test-signature`;
      return route.fulfill({ headers, json: { access_token: 'browser-access-token', id_token: token, token_type: 'Bearer', expires_in: 1800 } });
    }
    throw new Error(`Unexpected identity request: ${url.pathname}`);
  });
  return { exchanges: () => exchanges };
}

test('Factory login follows discovery and PKCE callback, restores a protected route, and exposes the exchanged token', async ({ page }) => {
  await mockBackend(page);
  const identity = await mockIdentityProvider(page);
  await page.goto('/apps?tab=build');
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page).toHaveURL(`${appOrigin}/apps?tab=build`);
  await expect.poll(() => page.evaluate(async () => (await window.mozaiksAuth.getCurrentUser())?.id)).toBe('browser-user');
  expect(await page.evaluate(() => window.mozaiksAuth.getAccessToken())).toBe('browser-access-token');
  expect(identity.exchanges()).toBe(1);
});

test('a signed-in person gets Profile and Sign Out in the account menu', async ({ page }) => {
  const config = shellConfig();
  config.profile = { show: true, menu: studioProfileMenu };
  await mockBackend(page, config);
  await mockIdentityProvider(page);
  await page.goto('/apps');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByTitle('Browser User').click();
  for (const label of ['Profile', 'Admin Portal', 'Sign Out']) {
    await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
  }
});

test('with authentication disabled the account menu offers no Profile or Sign Out', async ({ page }) => {
  const config = localModeShellConfig();
  config.profile = { show: true, menu: studioProfileMenu };
  await mockBackend(page, config);
  await page.goto('/apps');
  await page.getByTitle('Local User').click();
  await expect(page.getByRole('button', { name: 'Admin Portal', exact: true })).toBeVisible();
  for (const label of ['Profile', 'Account', 'Sign Out', 'Sign In']) {
    await expect(page.getByRole('button', { name: label, exact: true })).toHaveCount(0);
  }
});

test('with authentication disabled and only personal entries there is no account menu', async ({ page }) => {
  await mockBackend(page, localModeShellConfig());
  await page.goto('/apps');
  const banner = page.getByRole('banner');
  await expect(banner.getByRole('button', { name: 'Notifications' })).toBeVisible();
  await expect.poll(() => page.evaluate(async () => (await window.mozaiksAuth?.getCurrentUser())?.id)).toBe('configured-local-user');
  await expect(banner.getByTitle('Local User')).toHaveCount(0);
  for (const label of ['Profile', 'Account', 'Sign Out', 'Sign In']) {
    await expect(page.getByRole('button', { name: label, exact: true })).toHaveCount(0);
  }
});

test('a forged callback shows failure without authenticated identity', async ({ page }) => {
  await mockBackend(page);
  await page.goto('/auth/callback?code=unsolicited&state=unknown');
  await expect(page.getByRole('heading', { name: 'Sign-in unsuccessful' })).toBeVisible();
  expect(await page.evaluate(() => window.mozaiksAuth.getAccessToken())).toBeNull();
  await page.getByRole('button', { name: 'Return to sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toBeVisible();
});

test('failed auth bootstrap does not expose a demo adapter', async ({ page }) => {
  await page.route('**/api/shell-config', route => route.fulfill({ status: 503, json: { detail: 'unavailable' } }));
  await page.goto('/apps');
  await expect(page.getByRole('heading', { name: 'Unable to open this app' })).toBeVisible();
  expect(await page.evaluate(() => window.mozaiksAuth)).toBeUndefined();
});

test('explicit local mode uses the canonical backend user and sends no invented token', async ({ page }) => {
  await mockBackend(page, localModeShellConfig());
  await page.goto('/apps');
  await expect.poll(() => page.evaluate(async () => (await window.mozaiksAuth?.getCurrentUser())?.id)).toBe('configured-local-user');
  expect(await page.evaluate(() => window.mozaiksAuth.getAccessToken())).toBeNull();
  await expect(page.getByRole('heading', { name: 'Sign in', exact: true })).toHaveCount(0);
});

test('a public route renders without a user or fabricated bearer token', async ({ page }) => {
  const config = shellConfig();
  config.auth.required = false;
  config.auth.contract = null;
  config.auth.frontend = null;
  config.landing_spot = '/public';
  config.pages = [{ path: '/public', component: 'SchemaPage', schema: 'welcome', meta: { requiresAuth: false, appShell: false } }];
  await mockBackend(page, config);
  await page.route('**/api/pages/welcome', route => route.fulfill({ json: {
    name: 'welcome', route: '/public', layout: 'full-width', roles: [],
    sections: [{ id: 'welcome-heading', primitive: 'PageHeader', config: { title: 'Public information', subtitle: 'Available without signing in.' } }],
  } }));
  await page.goto('/public');
  await expect(page.getByRole('heading', { name: 'Public information' })).toBeVisible();
  expect(await page.evaluate(() => window.mozaiksAuth.getCurrentUser())).toBeNull();
  expect(await page.evaluate(() => window.mozaiksAuth.getAccessToken())).toBeNull();
});
