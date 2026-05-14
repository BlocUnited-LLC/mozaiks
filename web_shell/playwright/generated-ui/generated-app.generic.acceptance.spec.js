import fs from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';
import { parse } from 'yaml';

const appRoot = process.env.MOZAIKS_GENERATED_UI_APP_ROOT;

test.skip(!appRoot, 'Set MOZAIKS_GENERATED_UI_APP_ROOT to run generic generated app acceptance.');

function readJson(filePath, fallback = {}) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return fallback;
  }
}

function collectPageSchemas(root) {
  const pageRoot = path.join(root, 'ui', 'pages');
  if (!fs.existsSync(pageRoot)) return {};

  const schemas = {};
  for (const entry of fs.readdirSync(pageRoot)) {
    if (!entry.endsWith('.yaml') && !entry.endsWith('.yml')) continue;
    const fullPath = path.join(pageRoot, entry);
    const schemaName = path.basename(entry, path.extname(entry));
    schemas[schemaName] = parse(fs.readFileSync(fullPath, 'utf8'));
  }
  return schemas;
}

function collectApiEndpoints(value, endpoints = []) {
  if (!value || typeof value !== 'object') return endpoints;
  if (Array.isArray(value)) {
    value.forEach((item) => collectApiEndpoints(item, endpoints));
    return endpoints;
  }
  if (typeof value.api_endpoint === 'string' && value.api_endpoint.startsWith('/api/')) {
    endpoints.push({
      endpoint: value.api_endpoint,
      dataKey: typeof value.data_key === 'string' ? value.data_key : null,
    });
  }
  Object.values(value).forEach((item) => collectApiEndpoints(item, endpoints));
  return endpoints;
}

function pageTitle(schema) {
  const header = (schema.sections || []).find((section) => section?.primitive === 'PageHeader');
  return header?.config?.title || schema.title || schema.name || 'Page';
}

function shellConfigFor(app, schemas) {
  const pages = Object.entries(schemas).map(([schemaName, schema], index) => ({
    path: schema.route || `/${schemaName}`,
    component: 'SchemaPage',
    schema: schemaName,
    label: schema.title || schema.name || schemaName,
    order: (index + 1) * 10,
    meta: { title: schema.title || schema.name || schemaName, requiresAuth: false },
  }));
  const landingSpot = app?.startup?.landing_spot || app?.default_route || pages[0]?.path || '/';
  return {
    version: '1.0.0',
    appName: app?.appName || app?.app_name || 'Generated App',
    appId: app?.appId || app?.app_id || 'generated-app',
    landing_spot: landingSpot,
    pages,
    header: {
      logo: { src: null, wordmark: app?.appName || app?.app_name || 'Generated App', alt: 'Generated App', href: landingSpot },
      pages: [],
      actions: [],
    },
    notifications: { show: false },
    profile: { show: false },
    footer: { visible: false },
    mobile: { bottomBar: { visible: 'auto', items: [] } },
  };
}

function browserFailures(page) {
  const failures = [];
  page.on('console', (message) => {
    if (message.type() === 'error') failures.push(`console.error: ${message.text()}`);
  });
  page.on('pageerror', (error) => failures.push(`pageerror: ${error.message}`));
  return failures;
}

async function mockGeneratedApp(page, root, schemas) {
  const app = readJson(path.join(root, 'app.json'));
  const theme = readJson(path.join(root, 'brand', 'theme_config.json'), {});
  const shell = shellConfigFor(app, schemas);
  const endpoints = Object.values(schemas).flatMap((schema) => collectApiEndpoints(schema));

  await page.route('**/api/shell-config', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(shell) }),
  );
  await page.route('**/api/theme-config', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(theme) }),
  );
  await page.route('**/api/themes/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(theme) }),
  );
  await page.route('**/api/notifications/count', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ count: 0, unread_count: 0 }) }),
  );
  await page.route('**/api/workflows', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) }),
  );
  await page.route('**/api/pages/*', (route) => {
    const url = new URL(route.request().url());
    const schemaName = decodeURIComponent(url.pathname.split('/').pop() || '');
    const schema = schemas[schemaName];
    return route.fulfill({
      status: schema ? 200 : 404,
      contentType: 'application/json',
      body: JSON.stringify(schema || { detail: `Unknown page ${schemaName}` }),
    });
  });

  for (const { endpoint, dataKey } of endpoints) {
    await page.route(`**${endpoint}`, (route) => {
      const body = dataKey ? { [dataKey]: [] } : [];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
  }
}

test('generated app pages render without browser-level regressions', async ({ page }) => {
  const schemas = collectPageSchemas(appRoot);
  expect(Object.keys(schemas).length).toBeGreaterThan(0);

  await mockGeneratedApp(page, appRoot, schemas);
  const failures = browserFailures(page);

  for (const schema of Object.values(schemas)) {
    const route = schema.route || `/${schema.name}`;
    await page.goto(route);
    const main = page.locator('main');
    await expect(main).toBeVisible();
    await expect(main.getByRole('heading', { name: pageTitle(schema) }).first()).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${route} has horizontal overflow`).toBeLessThanOrEqual(2);
  }

  expect(failures, failures.join('\n')).toEqual([]);
});
