import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Drives an already-running Studio with real LLM and image calls. No webServer:
// seed a build with scripts/seed_app_media_smoke.py first (see app-media.spec.js).
export default defineConfig({
  testDir: __dirname,
  testMatch: /app-media\.spec\.js/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  timeout: 15 * 60 * 1000,
  expect: { timeout: 60 * 1000 },
  outputDir: process.env.MEDIA_SMOKE_OUTPUT_DIR || path.join(__dirname, '..', 'test-results', 'app-media'),
  use: {
    baseURL: process.env.STUDIO_URL || 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    viewport: { width: 1440, height: 900 },
    actionTimeout: 60 * 1000,
  },
});
