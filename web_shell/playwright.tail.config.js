import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Live diagnostic against a REAL stack started by hand:
//   backend  uvicorn mozaiksai.hosts.studio:app  :8100  (isolated worktree venv,
//                                                        --log-level info, log teed to a file)
//   frontend vite dev                            :3000  (proxies /api + /ws)
// The timeout is deliberately long: the question under test is whether a build
// that looks dead is actually working, and a short timeout cannot tell those apart.
export default defineConfig({
  testDir: path.join(__dirname, 'playwright'),
  testMatch: /live\.tail\.spec\.js/,
  fullyParallel: false,
  retries: 0,
  workers: 1,
  timeout: 88 * 60 * 1000,
  expect: { timeout: 30 * 1000 },
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:3100',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    actionTimeout: 60 * 1000,
    navigationTimeout: 120 * 1000,
  },
  projects: [
    {
      name: 'desktop-chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
});
