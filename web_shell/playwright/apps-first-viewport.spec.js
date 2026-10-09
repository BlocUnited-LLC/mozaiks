import { localDevelopmentAuth } from './fixtures/localAuth.js';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const readConfig = relativePath => JSON.parse(fs.readFileSync(path.join(repoRoot, relativePath), 'utf8'));
const appConfig = readConfig('factory_app/app/app.json');
const shellConfig = readConfig('factory_app/app/config/shell.json');
const themeConfig = readConfig('factory_app/app/brand/theme_config.json');
const routeManifest = readConfig('factory_app/app/ui/route_manifest.json');
const extensionRegistry = readConfig('factory_app/workflows/extended_orchestration/extension_registry.json');
const transitionRoutes = (extensionRegistry.entrypoints || []).map(entrypoint => ({
  ...entrypoint,
  meta: {
    ...(entrypoint.meta || {}),
    appShell: true,
    shellMode: extensionRegistry.transitions?.find(item => item.id === entrypoint.transition)?.ui?.shell_mode,
  },
}));
const composedShellConfig = {
  auth: localDevelopmentAuth,
  ...shellConfig,
  appId: appConfig.appId,
  appName: appConfig.appName,
  pages: [...(routeManifest.pages || []), ...transitionRoutes],
};
const apps = [
  {
    build_registry_id: 'demo_campaign_revision', app_id: 'campaign-revision-workbench',
    name: 'Campaign Revision Workbench', description: 'Release revision blocked on stakeholder feedback.',
    status: 'needs_revision', chat_app_id: 'factory-session-app', active_chat_id: 'campaign-revision-chat',
    active_workflow_id: 'AppGenerator', created_at: '2025-02-01T09:00:00Z', updated_at: '2025-02-04T18:25:00Z',
  },
  {
    build_registry_id: 'demo_partner_delivery', app_id: 'partner-delivery-studio',
    name: 'Partner Delivery Studio', description: 'Partner rollout, managed deployment, and release checks.',
    status: 'deploying', created_at: '2025-01-19T08:00:00Z', updated_at: '2025-02-05T11:20:00Z',
  },
  {
    build_registry_id: 'demo_member_growth', app_id: 'member-growth-studio',
    name: 'Member Growth Studio', description: 'Live growth insights, campaign prompts, and operator alerts.',
    status: 'active', created_at: '2025-01-10T13:10:00Z', updated_at: '2025-02-05T16:40:00Z',
  },
];

test('Apps shows a real app before the mobile bottom navigation', async ({ page }, testInfo) => {
  await page.route('**/api/shell-config', route => route.fulfill({ json: composedShellConfig }));
  await page.route('**/api/theme-config', route => route.fulfill({ json: themeConfig }));
  await page.route('**/api/themes/**', route => route.fulfill({ status: 404, json: {} }));
  await page.route('**/api/studio/apps', route => route.fulfill({ json: { apps, metrics: {} } }));
  await page.route('**/api/studio/dashboard**', route => route.fulfill({ json: {
    schema_version: 'mozaiks.dashboard.v1',
    workspace: { scope: 'workspace', route_pattern: '/apps', default_portal: 'portfolio',
      portals: [{ id: 'portfolio', label: 'Apps', route: '/apps', enabled: true }] },
    app: { scope: 'app', route_pattern: '/apps/:appId', default_portal: 'overview',
      portals: [{ id: 'overview', label: 'Overview', route: '/apps/:appId/overview', enabled: true }] },
  } }));
  await page.route('**/api/modules/user_onboarding/get_onboarding_status**', route => route.fulfill({ json: {
    seen_welcome: true, dismissed: true, progress: 0, completed_at: null,
    steps: { create_app: { completed: false }, explore_apps: { completed: false }, open_support: { completed: false } },
  } }));
  await page.goto('/apps');

  const main = page.locator('main');
  await expect(main.getByPlaceholder('Search apps...')).toBeVisible();
  const viewport = page.viewportSize();
  if (viewport.width < 768) {
    await expect(main.getByLabel('Summary metrics')).toBeHidden();
    const firstAppName = main.locator('article').first().getByText('Campaign Revision Workbench', { exact: true });
    await expect(firstAppName).toBeVisible();
    const appNameBox = await firstAppName.boundingBox();
    const bottomNavBox = await page.getByRole('navigation', { name: 'Mobile app navigation' }).boundingBox();
    expect(appNameBox).not.toBeNull();
    expect(bottomNavBox).not.toBeNull();
    expect(appNameBox.y + appNameBox.height).toBeLessThan(bottomNavBox.y);
  } else {
    await expect(main.getByLabel('Summary metrics')).toBeVisible();
    await expect(main.getByRole('row', { name: /Campaign Revision Workbench/ }).first()).toBeVisible();
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width + 1);
  if (process.env.UI_QA_SCREENSHOT_DIR) {
    fs.mkdirSync(process.env.UI_QA_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({
      path: path.join(process.env.UI_QA_SCREENSHOT_DIR, `${testInfo.project.name}-apps-first-viewport.png`),
      fullPage: false,
    });
  }
});
