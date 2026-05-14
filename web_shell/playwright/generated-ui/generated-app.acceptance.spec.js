import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';
import { parse } from 'yaml';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const usesExternalGeneratedApp = Boolean(process.env.MOZAIKS_GENERATED_UI_APP_ROOT);
const fixtureRoot = path.resolve(__dirname, '..', 'fixtures', 'generated-app', 'app');
const pageRoot = path.join(fixtureRoot, 'ui', 'pages');
const themeConfig = JSON.parse(
  fs.readFileSync(path.join(fixtureRoot, 'brand', 'theme_config.json'), 'utf8'),
);

const pageSchemas = {
  tickets: parse(fs.readFileSync(path.join(pageRoot, 'tickets.yaml'), 'utf8')),
  settings: parse(fs.readFileSync(path.join(pageRoot, 'settings.yaml'), 'utf8')),
};

const shellConfig = {
  version: '1.0.0',
  appName: 'Support Operations',
  appId: 'support-operations',
  landing_spot: '/tickets',
  pages: [
    {
      path: '/tickets',
      component: 'SchemaPage',
      schema: 'tickets',
      label: 'Tickets',
      order: 10,
      meta: { title: 'Tickets', requiresAuth: false },
    },
    {
      path: '/settings',
      component: 'SchemaPage',
      schema: 'settings',
      label: 'Settings',
      order: 20,
      meta: { title: 'Settings', requiresAuth: false },
    },
  ],
  header: {
    logo: { src: null, wordmark: 'Support Operations', alt: 'Support Operations', href: '/tickets' },
    pages: [],
    actions: [],
  },
  notifications: { show: false, path: '/notifications' },
  profile: { show: false },
  footer: { visible: false },
  mobile: { bottomBar: { visible: 'auto', items: [] } },
};

const ticketRows = [
  {
    id: 'ticket-001',
    subject: 'Acme renewal question',
    status: 'Open',
    owner: 'Maya Chen',
    updated_at: '2026-05-12T17:10:00Z',
  },
  {
    id: 'ticket-002',
    subject: 'Billing escalation',
    status: 'Review',
    owner: 'Nolan Brooks',
    updated_at: '2026-05-11T14:45:00Z',
  },
  {
    id: 'ticket-003',
    subject: 'Import failed for CSV upload',
    status: 'Resolved',
    owner: 'Iris Patel',
    updated_at: '2026-05-10T09:15:00Z',
  },
];

function collectBrowserFailures(page) {
  const failures = [];
  page.on('console', (message) => {
    if (message.type() === 'error') {
      failures.push(`console.error: ${message.text()}`);
    }
  });
  page.on('pageerror', (error) => {
    failures.push(`pageerror: ${error.message}`);
  });
  return failures;
}

async function expectNoBrowserFailures(failures) {
  expect(failures, failures.join('\n')).toEqual([]);
}

async function expectNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(2);
}

async function expectSomeVisibleText(container, text) {
  const visible = await container.getByText(text).evaluateAll((elements) =>
    elements.some((element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    }),
  );
  expect(visible).toBe(true);
}

async function mockGeneratedAppApis(page) {
  await page.route('**/api/shell-config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(shellConfig),
    });
  });

  await page.route('**/api/theme-config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(themeConfig),
    });
  });

  await page.route('**/api/themes/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(themeConfig),
    });
  });

  await page.route('**/api/notifications/count', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ count: 0, unread_count: 0 }),
    });
  });

  await page.route('**/api/workflows', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  await page.route('**/api/pages/*', async (route) => {
    const url = new URL(route.request().url());
    const schemaName = decodeURIComponent(url.pathname.split('/').pop() || '');
    const schema = pageSchemas[schemaName];
    if (!schema) {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: `Unknown fixture page ${schemaName}` }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(schema),
    });
  });

  await page.route('**/api/modules/tickets/list', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tickets: ticketRows }),
    });
  });

  await page.route('**/api/modules/settings/update', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true }),
    });
  });
}

test.beforeEach(async ({ page }) => {
  test.skip(usesExternalGeneratedApp, 'Fixture-specific assertions are skipped for external generated app roots.');
  await mockGeneratedAppApis(page);
});

test('generated tickets page renders clean primitive UI across viewports', async ({ page }) => {
  const failures = collectBrowserFailures(page);

  await page.goto('/tickets');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Support Tickets' })).toBeVisible();
  await expect(main.getByText('Review active support work and route customer issues.')).toBeVisible();
  await expect(main.getByRole('button', { name: 'New Ticket' })).toBeVisible();
  await expect(main.getByPlaceholder('Search…')).toBeVisible();
  await expectSomeVisibleText(main, 'Acme renewal question');
  await expectSomeVisibleText(main, 'Billing escalation');
  await expectNoHorizontalOverflow(page);

  await main.getByPlaceholder('Search…').fill('billing');
  await expectSomeVisibleText(main, 'Billing escalation');
  await expect(main.getByText('Acme renewal question')).toHaveCount(0);
  await expectNoHorizontalOverflow(page);

  await expectNoBrowserFailures(failures);
});

test('generated settings form submits through declarative action contract', async ({ page }) => {
  const failures = collectBrowserFailures(page);
  let submittedPayload = null;

  await page.route('**/api/modules/settings/update', async (route) => {
    submittedPayload = JSON.parse(route.request().postData() || '{}');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true }),
    });
  });

  await page.goto('/settings');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Settings' })).toBeVisible();
  await expect(main.getByLabel('App Name')).toHaveValue('Support Operations');
  await expect(main.getByLabel('Support Email')).toHaveValue('support@example.com');

  await main.getByLabel('App Name').fill('Support Desk');
  await main.getByLabel('Support Email').fill('ops@example.com');
  await main.getByRole('button', { name: 'Save Settings' }).click();

  await expect.poll(() => submittedPayload).toMatchObject({
    app_name: 'Support Desk',
    support_email: 'ops@example.com',
  });
  await expectNoHorizontalOverflow(page);
  await expectNoBrowserFailures(failures);
});
