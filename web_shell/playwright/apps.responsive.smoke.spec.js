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
const APP_ID = 'campaign-revision-workbench';

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
      app_id: 'partner-delivery-console',
      name: 'Partner Delivery Console',
      description: 'Partner rollout, managed deployment, and release checks.',
      status: 'deploying',
      created_at: '2025-01-19T08:00:00Z',
      updated_at: '2025-02-05T11:20:00Z',
    },
    {
      build_registry_id: 'demo_member_growth',
      app_id: 'member-growth-console',
      name: 'Member Growth Console',
      description: 'Live growth insights, campaign prompts, and operator alerts.',
      status: 'active',
      created_at: '2025-01-10T13:10:00Z',
      updated_at: '2025-02-05T16:40:00Z',
    },
  ],
  metrics: {},
};

function getWorkspaceApp(appId = APP_ID) {
  return appsPayload.apps.find((app) => app.app_id === appId) ?? appsPayload.apps[0];
}

function buildAppConsolePayload(appId = APP_ID) {
  const app = getWorkspaceApp(appId);

  return {
    summary: {
      app: {
        ...app,
        lifecycle_state: 'deploying',
        lifecycle_label: 'Deploying',
      },
      admin: {
        admins: ['ops@mozaiks.ai'],
      },
      workspace: {
        workflow_names: ['RevisionOrchestrator', 'DeployGuard'],
        runtime_readiness: 'entry_point_configured',
      },
    },
    stats: {
      tracked_chats: 14,
      total_cost: 242.5,
      total_errors: 2,
      total_tool_calls: 5,
      total_prompt_tokens: 3200,
      total_completion_tokens: 1800,
      total_agent_turns: 9,
    },
    runs: {
      runs: [
        {
          chat_id: 'run-1',
          workflow_name: 'RevisionOrchestrator',
          errors: 2,
          tool_calls: 3,
          cost: 123.4,
          prompt_tokens: 1400,
          completion_tokens: 620,
          user_id: 'ops@mozaiks.ai',
          runtime_sec: 18,
          started_at: '2025-02-05T09:30:00Z',
          ended_at: '2025-02-05T09:40:00Z',
        },
        {
          chat_id: 'run-2',
          workflow_name: 'DeployGuard',
          errors: 0,
          tool_calls: 2,
          cost: 89.1,
          prompt_tokens: 1100,
          completion_tokens: 710,
          user_id: 'release@mozaiks.ai',
          runtime_sec: 27,
          started_at: '2025-02-05T08:00:00Z',
        },
      ],
      total: 2,
    },
    sessions: {
      sessions: [],
      total: 0,
    },
    buildState: {
      build: {
        approval_state: 'pending',
      },
    },
    buildHistory: {
      artifact_versions: [
        {
          id: 'ver-17',
          version_number: 17,
          lifecycle_status: 'deployed',
          validation_status: 'passed',
          created_at: '2025-02-05T08:50:00Z',
        },
        {
          id: 'ver-16',
          version_number: 16,
          lifecycle_status: 'awaiting_review',
          validation_status: 'pending',
          created_at: '2025-02-04T17:20:00Z',
        },
      ],
    },
    integrations: {
      app_connectors: [
        {
          service: 'stripe',
          display_name: 'Stripe',
          notes: 'Payments enabled for this app.',
          secret_available: true,
        },
      ],
    },
    activity: [],
  };
}

async function mockConsoleApis(page) {
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
      body: JSON.stringify({ count: 9, unread_count: 9 }),
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

  await page.route('**/api/studio/overview?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppConsolePayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.summary),
    });
  });

  await page.route('**/api/admin/stats*', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppConsolePayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.stats),
    });
  });

  await page.route('**/api/admin/runs*', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppConsolePayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.runs),
    });
  });

  await page.route('**/api/admin/sessions?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppConsolePayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.sessions),
    });
  });

  await page.route('**/api/studio/build?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppConsolePayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.buildState),
    });
  });

  await page.route('**/api/studio/build/history?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppConsolePayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.buildHistory),
    });
  });

  await page.route('**/api/studio/integrations?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppConsolePayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.integrations),
    });
  });
}

async function expectNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(2);
}

test.beforeEach(async ({ page }) => {
  await mockConsoleApis(page);
});

test('apps route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto('/apps');
  const main = page.locator('main');

  await expect(page.getByRole('heading', { name: 'Apps' })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Create App' })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Import App' })).toBeVisible();
  await expect(main.getByPlaceholder('Search apps...')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Updated' })).toBeHidden();
    await expect(main.getByRole('button', { name: 'Continue Build' }).first()).toBeVisible();
    await expect(main.getByRole('button', { name: 'Open Console' }).first()).toBeVisible();

    const widgetButton = page.locator('.widget-safe-bottom button').first();
    await expect(widgetButton).toBeVisible();
    const widgetBox = await widgetButton.boundingBox();
    expect(widgetBox).not.toBeNull();
    expect(widgetBox.width).toBeLessThanOrEqual(52);
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
    await expect(page.getByRole('columnheader', { name: 'Updated' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Action' })).toBeVisible();
    await expect(main.getByRole('row', { name: /Campaign Revision Workbench/i }).first()).toBeVisible();
    await expect(main.getByRole('row', { name: /Partner Delivery Console/i }).first()).toBeVisible();

    const widgetButton = page.locator('.widget-safe-bottom button').first();
    await expect(widgetButton).toBeVisible();
    const widgetBox = await widgetButton.boundingBox();
    expect(widgetBox).not.toBeNull();
    expect(widgetBox.width).toBeGreaterThanOrEqual(72);
  }
});

test('import app overlay stays within the viewport and suppresses the floating widget', async ({ page }) => {
  await page.goto('/apps');
  const main = page.locator('main');

  const widgetRoot = page.locator('.widget-safe-bottom').first();
  await expect(widgetRoot).toBeVisible();

  await main.getByRole('button', { name: 'Import App' }).click();

  const dialog = page.getByRole('dialog', { name: 'Import App' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Close overlay' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expect(widgetRoot).toHaveAttribute('aria-hidden', 'true');

  const dialogBox = await dialog.boundingBox();
  const viewport = page.viewportSize();
  expect(dialogBox).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(dialogBox.width).toBeLessThanOrEqual(viewport.width);
  expect(dialogBox.y + dialogBox.height).toBeLessThanOrEqual(viewport.height + 1);
});

test('workspace usage route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto('/usage');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Usage', exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Export CSV' })).toBeVisible();
  await expect(main.getByPlaceholder('Search workflows or apps...')).toBeVisible();
  await expect(main.getByText('Tokens Used')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(main.getByRole('columnheader', { name: 'Workflow' })).toBeVisible();
    await expect(main.getByRole('columnheader', { name: 'Input' })).toBeVisible();
    await expect(main.getByRole('row', { name: /RevisionOrchestrator Workspace/i }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }
});

test('workspace health route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto('/health');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Health', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Health by app' })).toBeVisible();
  await expect(main.getByPlaceholder('Search health...')).toBeVisible();
  await expect(main.getByText('Campaign Revision Workbench')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }
});

test('workspace billing route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto('/billing');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Billing', exact: true })).toBeVisible();
  await expect(main.getByPlaceholder('Search billing...')).toBeVisible();
  await expect(main.getByText('Billing reporting pending')).toBeVisible();
  await expect(main.getByText('Total Revenue')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }
});

test('app console root redirects to overview', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}`);

  await expect(page).toHaveURL(new RegExp(`/apps/${APP_ID}/overview$`));
  await expect(page.locator('main').getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
});

test('app overview route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/overview`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Latest app movement' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Workflow coverage' })).toBeVisible();
  await expect(main.getByText('RevisionOrchestrator').first()).toBeVisible();
  await expect(main.getByText('Build version 17').first()).toBeVisible();
  await expect(main.getByText('Pending Approvals')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }
});

test('app integrations route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/integrations`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Integrations', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Enabled and disabled integrations' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Used by agents and workflows' })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Add Integration' })).toBeVisible();
  await expect(main.getByText('Stripe').first()).toBeVisible();
  await main.getByRole('button', { name: 'Add Integration' }).click();
  await expect(page.getByText('Register a new external service for this app.')).toBeVisible();
  await expect(page.locator('input[placeholder="stripe"]').first()).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }
});

test('app usage route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/usage`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Usage', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Workflow token breakdown' })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Export CSV' })).toBeVisible();
  await expect(main.getByRole('columnheader', { name: 'Input' })).toBeVisible();
  await expect(main.getByText('RevisionOrchestrator').first()).toBeVisible();
  await expect(main.getByText('Average latency')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }
});

test('app health route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/health`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Health', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Current app health' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Workflow reliability' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Integration posture' })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }
});

test('app billing route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/billing`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Billing', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Revenue status' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Plans and subscriptions' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Payments and refunds' })).toBeVisible();
  await expect(main.getByRole('link', { name: 'Review Hosting' })).toBeVisible();
  await expect(main.getByRole('link', { name: 'Connect Payments' })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }
});

test('app users route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/users`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Users', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'People using this app' })).toBeVisible();
  await expect(main.getByPlaceholder('Search users')).toBeVisible();
  await expect(main.getByRole('button', { name: 'Export Users' })).toBeVisible();
  await expect(main.getByText('No user records yet')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }
});

test('mobile app console navigation keeps route transitions stable', async ({ page }) => {
  const viewport = page.viewportSize();
  test.skip(!viewport || viewport.width >= 768, 'Mobile app-console navigation smoke only applies to the mobile project.');

  await page.goto(`/apps/${APP_ID}/overview`);
  const main = page.locator('main');
  const routeChecks = [
    {
      href: `/apps/${APP_ID}/health`,
      heading: 'Health',
      detail: async () => expect(main.getByRole('heading', { name: 'Current app health' })).toBeVisible(),
    },
    {
      href: `/apps/${APP_ID}/usage`,
      heading: 'Usage',
      detail: async () => expect(main.getByRole('heading', { name: 'Workflow token breakdown' })).toBeVisible(),
    },
    {
      href: `/apps/${APP_ID}/billing`,
      heading: 'Billing',
      detail: async () => expect(main.getByRole('heading', { name: 'Revenue status' })).toBeVisible(),
    },
    {
      href: `/apps/${APP_ID}/users`,
      heading: 'Users',
      detail: async () => expect(main.getByRole('heading', { name: 'People using this app' })).toBeVisible(),
    },
  ];

  for (const routeCheck of routeChecks) {
    await page.getByRole('button', { name: 'Open console navigation' }).click();

    const navigation = page.getByRole('navigation', { name: 'App Console navigation' });
    await expect(navigation).toBeVisible();
    await navigation.locator(`a[href="${routeCheck.href}"]`).click();

    await expect(main.getByRole('heading', { name: routeCheck.heading, exact: true })).toBeVisible();
    await routeCheck.detail();
    await expectNoHorizontalOverflow(page);
  }
});

test('mobile workspace console navigation keeps route transitions stable', async ({ page }) => {
  const viewport = page.viewportSize();
  test.skip(!viewport || viewport.width >= 768, 'Mobile workspace-console navigation smoke only applies to the mobile project.');

  await page.goto('/apps');
  const main = page.locator('main');
  const routeChecks = [
    {
      href: '/usage',
      heading: 'Usage',
      detail: async () => expect(main.getByRole('button', { name: 'Export CSV' })).toBeVisible(),
    },
    {
      href: '/health',
      heading: 'Health',
      detail: async () => expect(main.getByRole('heading', { name: 'Health by app' })).toBeVisible(),
    },
    {
      href: '/billing',
      heading: 'Billing',
      detail: async () => expect(main.getByText('Total Revenue')).toBeVisible(),
    },
    {
      href: '/apps',
      heading: 'Apps',
      detail: async () => expect(main.getByRole('button', { name: 'Create App' })).toBeVisible(),
    },
  ];

  for (const routeCheck of routeChecks) {
    await page.getByRole('button', { name: 'Open console navigation' }).click();

    const navigation = page.getByRole('navigation', { name: 'Workspace navigation' });
    await expect(navigation).toBeVisible();
    await navigation.locator(`a[href="${routeCheck.href}"]`).click();

    await expect(main.getByRole('heading', { name: routeCheck.heading, exact: true })).toBeVisible();
    await routeCheck.detail();
    await expectNoHorizontalOverflow(page);
  }
});
