import { defineConfig, devices } from '@playwright/test';
import { readFileSync } from 'node:fs';

if (!process.env.COMMUNITY_LIVE_CONFIG) {
  throw new Error('Start the community live environment and set COMMUNITY_LIVE_CONFIG to its environment.json.');
}
const environment = JSON.parse(readFileSync(process.env.COMMUNITY_LIVE_CONFIG, 'utf8'));
if (environment.status !== 'ready') {
  throw new Error('The community live environment must report ready before acceptance runs.');
}

export default defineConfig({
  testDir: './playwright/community',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120000,
  expect: { timeout: 15000 },
  reporter: [['list'], ['json', { outputFile: 'test-results/community-results.json' }]],
  use: {
    baseURL: environment.base_url,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'community-desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 960 } } },
    { name: 'community-phone', use: { ...devices['iPhone 13'], browserName: 'chromium' } },
  ],
});
