/**
 * Visual regression tests — Mozaiks web shell.
 *
 * Uses Playwright's built-in toHaveScreenshot() for pixel-diff comparison.
 * Baselines live in playwright/snapshots/ and must be committed to source control.
 *
 * To create or update baselines:
 *   npx playwright test --config playwright.visual.config.js --update-snapshots
 *
 * To run comparison in CI:
 *   npx playwright test --config playwright.visual.config.js
 *
 * Generated-app visual tests belong in the app's own test suite, not here.
 *
 * Dynamic content masking:
 *   Any element with data-testid="dynamic" or class "mz-dynamic" is masked in
 *   screenshots. Use these attributes on timestamps, counters, or user-specific
 *   content so they don't cause false positives.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..', '..');
const themeConfig = JSON.parse(
  fs.readFileSync(path.join(repoRoot, 'factory_app', 'app', 'brand', 'theme_config.json'), 'utf8'),
);
const shellConfig = JSON.parse(
  fs.readFileSync(path.join(repoRoot, 'factory_app', 'app', 'config', 'shell.json'), 'utf8'),
);
const routeManifest = JSON.parse(
  fs.readFileSync(path.join(repoRoot, 'factory_app', 'app', 'ui', 'route_manifest.json'), 'utf8'),
);
const composedShellConfig = {
  ...shellConfig,
  pages: routeManifest.pages || [],
};
const appsPayload = {
  apps: [
    {
      build_registry_id: 'demo_campaign_revision',
      app_id: 'campaign-revision-workbench',
      name: 'Campaign Revision Workbench',
      description: 'Release revision blocked on stakeholder feedback.',
      status: 'needs_revision',
      created_at: '2025-02-01T09:00:00Z',
      updated_at: '2025-02-04T18:25:00Z',
    },
    {
      build_registry_id: 'demo_partner_delivery',
      app_id: 'partner-delivery-studio',
      name: 'Partner Delivery Studio',
      description: 'Partner rollout, managed deployment, and release checks.',
      status: 'deploying',
      created_at: '2025-01-19T08:00:00Z',
      updated_at: '2025-02-05T11:20:00Z',
    },
    {
      build_registry_id: 'demo_member_growth',
      app_id: 'member-growth-studio',
      name: 'Member Growth Studio',
      description: 'Live growth insights, campaign prompts, and operator alerts.',
      status: 'active',
      created_at: '2025-01-10T13:10:00Z',
      updated_at: '2025-02-05T16:40:00Z',
    },
  ],
  metrics: {},
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Wait for the Mozaiks app shell to finish bootstrapping.
 * Waits for a known shell landmark (SkipLink or main content region) to appear.
 */
async function waitForAppShell(page) {
  // The SkipLink is the first element rendered; the main landmark appears after routing.
  await page.waitForSelector('#main-content, [data-testid="app-shell-ready"], a[href="#main-content"]', {
    timeout: 10_000,
  });
  // Extra tick to let any CSS transitions settle
  await page.waitForTimeout(300);
}

async function mockStudioApis(page) {
  await page.route('**/api/shell-config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(composedShellConfig),
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
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({}),
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

  await page.route('**/api/studio/apps', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(appsPayload),
    });
  });

  await page.route('**/api/general_chats/list/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ sessions: [], total: 0 }),
    });
  });

  await page.route('**/api/sessions/list/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ sessions: [], total: 0 }),
    });
  });
}

/**
 * Mask dynamic content areas before taking a screenshot.
 *
 * Elements with data-testid="dynamic" or the CSS class "mz-dynamic" are
 * replaced with a solid grey box so timestamps, live counters, and
 * user-specific content don't cause false positives.
 */
function dynamicMasks(page) {
  return page
    .locator('[data-testid="dynamic"], .mz-dynamic')
    .all()
    .then((els) => els);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('App shell visual regression', () => {
  test.beforeEach(async ({ page }) => {
    await mockStudioApis(page);
  });

  test('landing / login screen matches baseline', async ({ page }) => {
    await page.goto('/');
    await waitForAppShell(page);

    const masks = await dynamicMasks(page);
    await expect(page).toHaveScreenshot('landing.png', { mask: masks });
  });

  test('skip link is visible on keyboard focus', async ({ page }) => {
    await page.goto('/');
    await waitForAppShell(page);

    // Tab once to focus the SkipLink
    await page.keyboard.press('Tab');
    await page.waitForTimeout(150);

    const skipLink = page.locator('a[href="#main-content"]');
    await expect(skipLink).toBeVisible();
    const masks = await dynamicMasks(page);
    await expect(page).toHaveScreenshot('skip-link-focused.png', { mask: masks });
  });

  test('apps list page matches baseline', async ({ page }) => {
    await page.goto('/apps');
    await waitForAppShell(page);

    const masks = await dynamicMasks(page);
    await expect(page).toHaveScreenshot('apps-list.png', { mask: masks, maxDiffPixelRatio: 0.04 });
  });
});

test.describe('Dark / light theme visual regression', () => {
  test.beforeEach(async ({ page }) => {
    await mockStudioApis(page);
  });

  test('landing in light theme matches baseline', async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => document.documentElement.classList.remove('dark'));
    await waitForAppShell(page);

    const masks = await dynamicMasks(page);
    await expect(page).toHaveScreenshot('landing-light.png', { mask: masks });
  });

  test('landing in dark theme matches baseline', async ({ page }) => {
    await page.goto('/');
    await page.evaluate(() => document.documentElement.classList.add('dark'));
    await waitForAppShell(page);

    const masks = await dynamicMasks(page);
    await expect(page).toHaveScreenshot('landing-dark.png', { mask: masks });
  });
});
