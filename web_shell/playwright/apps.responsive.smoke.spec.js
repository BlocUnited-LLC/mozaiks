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
const INTEGRATIONS_QA_DIR = process.env.INTEGRATIONS_UI_QA_DIR
  || path.join(repoRoot, '.logs', 'ui-qa', 'integrations-health-check');
const INTEGRATIONS_QA_ENABLED = Boolean(process.env.INTEGRATIONS_UI_QA);
const SECRET_SENTINEL = 'test-secret-value';

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
          service: 'analytics_provider',
          display_name: 'Hosted Analytics',
          notes: 'Usage events are sent through this configured analytics connector.',
          secret_available: true,
          configured: true,
          ready: true,
          required_fields: [
            {
              name: 'api_key',
              label: 'API Key',
              type: 'secret',
              required: true,
              frontend_safe: false,
            },
            {
              name: 'endpoint_url',
              label: 'Endpoint URL',
              type: 'url',
              required: true,
              frontend_safe: true,
            },
            {
              name: 'workspace_id',
              label: 'Workspace ID',
              type: 'text',
              required: false,
              frontend_safe: true,
            },
          ],
          public_config: {
            endpoint_url: 'https://analytics.example.test',
            workspace_id: 'demo-workspace',
            api_key: SECRET_SENTINEL,
          },
          health: {
            status: 'configured',
            last_checked_at: '2026-05-17T12:00:00Z',
            message: 'Required configuration is present.',
            missing_fields: [],
            checked_by: 'readiness',
            health_check_supported: true,
            frontend_safe: true,
          },
        },
        {
          service: 'reporting_provider',
          display_name: 'Reporting Provider',
          notes: 'Scheduled report exports need this connector before workflow use.',
          secret_available: false,
          configured: false,
          ready: false,
          required_fields: [
            {
              name: 'api_key',
              label: 'API Key',
              type: 'secret',
              required: true,
              frontend_safe: false,
            },
            {
              name: 'endpoint_url',
              label: 'Endpoint URL',
              type: 'url',
              required: true,
              frontend_safe: true,
            },
          ],
          public_config: {},
          health: {
            status: 'not_configured',
            last_checked_at: '2026-05-17T12:05:00Z',
            message: 'Required connector fields are missing.',
            missing_fields: ['api_key', 'endpoint_url'],
            checked_by: 'readiness',
            health_check_supported: false,
            frontend_safe: true,
          },
        },
        {
          service: 'search_provider',
          display_name: 'Search Provider',
          notes: 'Search indexing can be enabled after this connector is reviewed.',
          secret_available: true,
          configured: false,
          ready: false,
          required_fields: [
            {
              name: 'api_key',
              label: 'API Key',
              type: 'secret',
              required: true,
              frontend_safe: false,
            },
          ],
          public_config: {
            endpoint_url: 'https://search.example.test',
          },
          health: {
            status: 'unhealthy',
            last_checked_at: '2026-05-17T12:10:00Z',
            message: 'Manual review required before workflow use.',
            missing_fields: [],
            checked_by: 'manual',
            health_check_supported: true,
            frontend_safe: true,
          },
        },
        {
          service: 'notification_provider',
          display_name: 'Notification Provider',
          notes: 'This connector has not been checked yet.',
          secret_available: false,
          configured: false,
          ready: false,
          required_fields: [],
          public_config: {},
          health: {
            status: 'unknown',
            last_checked_at: null,
            message: null,
            missing_fields: [],
            checked_by: null,
            health_check_supported: false,
            frontend_safe: true,
          },
        },
      ],
      connector_summary: {
        total: 4,
        configured: 1,
        healthy: 0,
        not_configured: 1,
        unhealthy: 1,
        unknown_health: 1,
      },
      runtime_integrations: {
        connector_vault: {
          configured: true,
        },
      },
    },
    activity: [],
  };
}

function buildWorkspaceRunsPayload() {
  const runs = appsPayload.apps.flatMap((app) => {
    const payload = buildAppConsolePayload(app.app_id);
    return (payload.runs?.runs || []).map((run) => ({
      ...run,
      app_id: app.app_id,
      app_name: app.name,
    }));
  });

  return {
    runs,
    total: runs.length,
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
    const appId = url.searchParams.get('app_id');
    const payload = appId ? buildAppConsolePayload(appId).runs : buildWorkspaceRunsPayload();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
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

async function writeIntegrationsQaArtifact(name, content) {
  if (!INTEGRATIONS_QA_ENABLED) return;
  fs.mkdirSync(INTEGRATIONS_QA_DIR, { recursive: true });
  const filePath = path.join(INTEGRATIONS_QA_DIR, name);
  if (typeof content === 'string') {
    fs.writeFileSync(filePath, content, 'utf8');
    return;
  }
  fs.writeFileSync(filePath, JSON.stringify(content, null, 2), 'utf8');
}

async function captureIntegrationsQa(page, testInfo, name, findings) {
  if (!INTEGRATIONS_QA_ENABLED) return;
  fs.mkdirSync(INTEGRATIONS_QA_DIR, { recursive: true });
  const projectName = testInfo.project.name.replace(/[^a-z0-9_-]+/gi, '-').toLowerCase();
  const screenshotPath = path.join(INTEGRATIONS_QA_DIR, `${projectName}-${name}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await writeIntegrationsQaArtifact(`${projectName}-${name}-report.json`, {
    page: `/apps/${APP_ID}/integrations`,
    screenshot: path.relative(repoRoot, screenshotPath).replace(/\\/g, '/'),
    viewport: page.viewportSize(),
    findings,
  });
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
    await expect(main.getByRole('columnheader', { name: 'App' })).toBeVisible();
    await expect(main.getByRole('columnheader', { name: 'Input tok.' })).toBeVisible();
    await expect(main.getByRole('row', { name: /Campaign Revision Workbench/i }).first()).toBeVisible();
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

test('app integrations route stays responsive across desktop and mobile widths', async ({ page }, testInfo) => {
  const consoleMessages = [];
  const responseFindings = [];
  const providerUrlRequests = [];
  let healthCheckRequests = 0;
  page.on('console', (message) => {
    if (['warning', 'error'].includes(message.type())) {
      consoleMessages.push({
        type: message.type(),
        text: message.text(),
      });
    }
  });
  page.on('response', (response) => {
    if (response.status() >= 400) {
      responseFindings.push({
        status: response.status(),
        url: response.url(),
      });
    }
  });
  page.on('request', (request) => {
    const requestUrl = request.url();
    if (requestUrl.includes('analytics.example.test') || requestUrl.includes('search.example.test')) {
      providerUrlRequests.push(requestUrl);
    }
  });
  await page.route('**/api/studio/integrations/connectors/*/health-check?**', async (route) => {
    healthCheckRequests += 1;
    const url = new URL(route.request().url());
    const service = decodeURIComponent(url.pathname.split('/connectors/')[1].split('/health-check')[0]);
    expect(url.searchParams.get('app_id')).toBe(APP_ID);
    await new Promise((resolve) => {
      setTimeout(resolve, 250);
    });
    if (service === 'search_provider') {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          error: 'Health check unavailable.',
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        app_id: APP_ID,
        service: 'analytics_provider',
        health: {
          status: 'healthy',
          last_checked_at: '2026-05-17T13:00:00Z',
          message: 'Manual health check passed.',
          missing_fields: [],
          checked_by: 'manual',
          safe_details: {
            response_code: 'ok',
          },
          health_check_supported: true,
          frontend_safe: true,
        },
      }),
    });
  });

  await page.goto(`/apps/${APP_ID}/integrations`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Integrations', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Enabled and disabled integrations' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Used by agents and workflows' })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Add Integration' })).toBeVisible();
  await expect(main.getByText('Hosted Analytics').first()).toBeVisible();
  await expect(main.getByText('Reporting Provider').first()).toBeVisible();
  await expect(main.getByText('Search Provider').first()).toBeVisible();
  await expect(main.getByText('Notification Provider').first()).toBeVisible();
  await expect(main.getByText('Configured').first()).toBeVisible();
  await expect(main.getByText('Missing setup').first()).toBeVisible();
  await expect(main.getByText('Needs attention').first()).toBeVisible();
  await expect(main.getByText('Unknown').first()).toBeVisible();
  await expect(main.getByText('Ready').first()).toBeVisible();
  await expect(main.getByText('Incomplete').first()).toBeVisible();
  await expect(main.getByText('Review').first()).toBeVisible();
  await expect(main.getByText('Missing required fields: API Key, Endpoint URL')).toBeVisible();
  await expect(main.getByText('Source readiness').first()).toBeVisible();
  await expect(main.getByText('Source manual').first()).toBeVisible();
  await expect(main.getByText('https://analytics.example.test')).toBeVisible();
  await expect(main.getByText('demo-workspace')).toBeVisible();
  await expect(main.getByRole('button', { name: 'Check now' })).toHaveCount(2);
  await expect(main.getByText(SECRET_SENTINEL)).toHaveCount(0);
  await expect(main.getByText('api key', { exact: true })).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  expect(healthCheckRequests).toBe(0);
  expect(providerUrlRequests).toHaveLength(0);

  await captureIntegrationsQa(page, testInfo, 'initial', {
    shell_layout: 'rendered',
    supported_connectors_show_check_now: true,
    unsupported_connectors_hide_check_now: true,
    health_check_requests_on_page_load: healthCheckRequests,
    provider_url_requests: providerUrlRequests,
    secret_values_visible: false,
    console_messages: consoleMessages,
    response_findings: responseFindings,
  });

  const firstCheckButton = main.getByRole('button', { name: 'Check now' }).first();
  await firstCheckButton.click();
  await expect(main.getByRole('button', { name: 'Checking...' })).toBeVisible();
  await captureIntegrationsQa(page, testInfo, 'checking', {
    state: 'checking',
    health_check_requests: healthCheckRequests,
    provider_url_requests: providerUrlRequests,
    secret_values_visible: false,
  });
  await expect(main.getByText('Manual health check passed.')).toBeVisible();
  await expect(main.getByText('response code')).toBeVisible();
  expect(healthCheckRequests).toBe(1);
  expect(providerUrlRequests).toHaveLength(0);

  await captureIntegrationsQa(page, testInfo, 'success', {
    shell_layout: 'rendered',
    summary_counts: ['Configured', 'Healthy', 'Missing required fields', 'Needs attention', 'Unknown'],
    connector_health_states: ['configured', 'not_configured', 'unhealthy', 'unknown'],
    public_config_visible: ['endpoint_url', 'workspace_id'],
    secret_values_visible: false,
    manual_health_check_requests: healthCheckRequests,
    console_messages: consoleMessages,
    response_findings: responseFindings,
  });

  await main.getByRole('button', { name: 'Check now' }).nth(1).click();
  await expect(main.getByText(/503|Health check failed/i)).toBeVisible();
  await expect(main.getByText(SECRET_SENTINEL)).toHaveCount(0);
  expect(healthCheckRequests).toBe(2);
  expect(providerUrlRequests).toHaveLength(0);

  await captureIntegrationsQa(page, testInfo, 'failure', {
    state: 'failure',
    safe_error_visible: true,
    secret_values_visible: false,
    provider_url_requests: providerUrlRequests,
    console_messages: consoleMessages,
    response_findings: responseFindings,
  });

  await writeIntegrationsQaArtifact(`${testInfo.project.name.replace(/[^a-z0-9_-]+/gi, '-').toLowerCase()}-report.json`, {
    page: `/apps/${APP_ID}/integrations`,
    states_verified: ['initial', 'checking', 'success', 'failure'],
    health_check_requests: healthCheckRequests,
    health_check_requests_on_page_load: 0,
    provider_url_requests: providerUrlRequests,
    secret_values_visible: false,
    supported_connectors_show_check_now: true,
    unsupported_connectors_hide_check_now: true,
    console_messages: consoleMessages,
    response_findings: responseFindings,
  });

  await main.getByRole('button', { name: 'Add Integration' }).click();
  await expect(page.getByText('Register a new external service for this app.')).toBeVisible();
  await expect(page.locator('input[placeholder="analytics_provider"]').first()).toBeVisible();
  await expect(page.locator('input[placeholder="Hosted Analytics"]').first()).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open console navigation' })).toBeHidden();
  }

  await captureIntegrationsQa(page, testInfo, 'add-integration-overlay', {
    shell_layout: 'rendered',
    overlay: 'add integration',
    secret_values_visible: false,
    console_messages: consoleMessages,
    response_findings: responseFindings,
  });
});

test('app usage route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/usage`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Usage', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Workflow breakdown' })).toBeVisible();
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
      detail: async () => expect(main.getByRole('heading', { name: 'Workflow breakdown' })).toBeVisible(),
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
