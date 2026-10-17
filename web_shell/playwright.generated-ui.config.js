import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { generatedUiMode, realHostSettings } from './playwright/fixtures/generatedUiRealHost.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const realHost = generatedUiMode() === 'real_host' ? realHostSettings(__dirname) : null;
const fixtureAppRoot = path.join(__dirname, 'playwright', 'fixtures', 'generated-app', 'app');
const activeAppRoot = process.env.MOZAIKS_GENERATED_UI_APP_ROOT
  ? path.resolve(process.env.MOZAIKS_GENERATED_UI_APP_ROOT)
  : fixtureAppRoot;

export default defineConfig({
  testDir: path.join(__dirname, 'playwright', 'generated-ui'),
  ...(realHost
    ? { testMatch: realHost.specPath.replaceAll('\\', '/') }
    : { testMatch: [
      '**/generated-app.acceptance.spec.js',
      '**/generated-app.generic.acceptance.spec.js',
      '**/entitlement-upgrade.spec.js',
      '**/token-depletion.spec.js',
    ] }),
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: realHost ? 0 : process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: realHost?.baseURL ?? 'http://127.0.0.1:4174',
    ...(realHost && process.env.MOZAIKS_GENERATED_UI_STORAGE_STATE
      ? { storageState: process.env.MOZAIKS_GENERATED_UI_STORAGE_STATE } : {}),
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  webServer: realHost ? undefined : {
    command: 'npm run build && npm run preview -- --host 127.0.0.1 --port 4174',
    cwd: __dirname,
    env: {
      ...process.env,
      PLATFORM_PATH: activeAppRoot,
      MOZAIKS_APP_WORKSPACE_PATH: activeAppRoot,
      MOZAIKS_GENERATED_UI_APP_ROOT: activeAppRoot,
    },
    url: 'http://127.0.0.1:4174/tickets',
    reuseExistingServer: !process.env.CI,
    timeout: 120000,
  },
  projects: [
    {
      name: 'generated-desktop',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: 'generated-mobile',
      use: {
        ...devices['iPhone 13'],
        browserName: 'chromium',
      },
    },
  ],
});
