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
 * Adding tests for your generated app:
 *   Import this spec's helpers (waitForAppShell, maskDynamicContent) in your own
 *   visual-regression spec and add page.goto('/your-route') tests alongside these.
 *   Generated-app visual tests belong in the app's own test suite, not here.
 *
 * Dynamic content masking:
 *   Any element with data-testid="dynamic" or class "mz-dynamic" is masked in
 *   screenshots. Use these attributes on timestamps, counters, or user-specific
 *   content so they don't cause false positives.
 */

import { expect, test } from '@playwright/test';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Wait for the Mozaiks app shell to finish bootstrapping.
 * Waits for a known shell landmark (SkipLink or main content region) to appear.
 */
async function waitForAppShell(page) {
  // The SkipLink is the first element rendered; the main landmark appears after routing.
  await page.waitForSelector('#main-content, [data-testid="app-shell-ready"]', {
    timeout: 10_000,
  });
  // Extra tick to let any CSS transitions settle
  await page.waitForTimeout(300);
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
    await expect(skipLink).toHaveScreenshot('skip-link-focused.png');
  });

  test('apps list page matches baseline', async ({ page }) => {
    await page.goto('/apps');
    await waitForAppShell(page);

    const masks = await dynamicMasks(page);
    await expect(page).toHaveScreenshot('apps-list.png', { mask: masks });
  });
});

test.describe('Dark / light theme visual regression', () => {
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
