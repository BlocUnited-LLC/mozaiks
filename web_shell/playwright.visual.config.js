/**
 * Playwright visual regression config.
 *
 * Uses Playwright's built-in toHaveScreenshot() for pixel-diff baseline comparison.
 * No third-party service required.
 *
 * Usage:
 *   # Update / create baselines
 *   npx playwright test --config playwright.visual.config.js --update-snapshots
 *
 *   # Compare against baselines (CI mode)
 *   npx playwright test --config playwright.visual.config.js
 *
 * Baselines are stored in playwright/snapshots/{project-name}/ and should be
 * committed to source control so CI can compare against them.
 *
 * Thresholds:
 *   maxDiffPixelRatio: 0.01  — up to 1% of pixels may differ (anti-aliasing, fonts)
 *   threshold:         0.2   — per-pixel colour channel tolerance (0–1 scale)
 *
 * Adjust these per-project if your build environment uses a different font
 * renderer or OS than the baseline machine.
 */
import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  testDir: path.join(__dirname, 'playwright'),
  testMatch: '**/visual-regression.spec.js',

  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report-visual' }]]
    : 'list',

  snapshotDir: path.join(__dirname, 'playwright', 'snapshots'),
  snapshotPathTemplate: '{snapshotDir}/{projectName}/{testFilePath}/{arg}{ext}',

  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01,
      threshold: 0.2,
    },
  },

  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },

  webServer: {
    command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 4173',
    cwd: __dirname,
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },

  projects: [
    {
      name: 'desktop-chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        // Deterministic font rendering for stable baselines across CI machines
        launchOptions: {
          args: [
            '--font-render-hinting=none',
            '--disable-font-subpixel-positioning',
          ],
        },
      },
    },
    {
      name: 'mobile-chromium',
      use: {
        ...devices['iPhone 13'],
        browserName: 'chromium',
      },
    },
  ],
});
