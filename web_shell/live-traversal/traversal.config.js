import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Drives the already-running Studio (vite dev :3000 -> backend :8000).
// No webServer on purpose: this exercises the real stack with real LLM calls
// and real Mongo, not a mocked preview build.
export default defineConfig({
  testDir: __dirname,
  testMatch: /traversal\.spec\.js/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  timeout: 45 * 60 * 1000,
  expect: { timeout: 60 * 1000 },
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    viewport: { width: 1440, height: 900 },
    actionTimeout: 60 * 1000,
  },
});
