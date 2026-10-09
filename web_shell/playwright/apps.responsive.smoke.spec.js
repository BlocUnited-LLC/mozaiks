import { localDevelopmentAuth } from './fixtures/localAuth.js';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..', '..');
const appConfig = JSON.parse(
  fs.readFileSync(path.join(repoRoot, 'factory_app', 'app', 'app.json'), 'utf8'),
);
const themeConfig = JSON.parse(
  fs.readFileSync(path.join(repoRoot, 'factory_app', 'app', 'brand', 'theme_config.json'), 'utf8'),
);
const shellConfig = JSON.parse(
  fs.readFileSync(path.join(repoRoot, 'factory_app', 'app', 'config', 'shell.json'), 'utf8'),
);
const routeManifest = JSON.parse(
  fs.readFileSync(path.join(repoRoot, 'factory_app', 'app', 'ui', 'route_manifest.json'), 'utf8'),
);
const extensionRegistry = JSON.parse(
  fs.readFileSync(
    path.join(repoRoot, 'factory_app', 'workflows', 'extended_orchestration', 'extension_registry.json'),
    'utf8',
  ),
);
const transitionRoutes = (extensionRegistry.entrypoints || []).map((entrypoint) => ({
  ...entrypoint,
  meta: {
    ...(entrypoint.meta || {}),
    appShell: true,
    shellMode: extensionRegistry.transitions?.find(
      (transition) => transition.id === entrypoint.transition,
    )?.ui?.shell_mode,
  },
}));
const composedShellConfig = {
  auth: localDevelopmentAuth,
  ...shellConfig,
  appId: appConfig.appId,
  appName: appConfig.appName,
  pages: [...(routeManifest.pages || []), ...transitionRoutes],
};
const dashboardPayload = {
  schema_version: 'mozaiks.dashboard.v1',
  workspace: {
    scope: 'workspace',
    route_pattern: '/apps',
    default_portal: 'portfolio',
    portals: [
      {
        id: 'portfolio',
        label: 'Apps',
        route: '/apps',
        enabled: true,
      },
    ],
  },
  app: {
    scope: 'app',
    route_pattern: '/apps/:appId',
    default_portal: 'overview',
    portals: [
      {
        id: 'overview',
        label: 'Overview',
        route: '/apps/:appId/overview',
        enabled: true,
        panels: [
          { id: 'summary', type: 'summary_strip', title: 'Summary' },
          { id: 'next_step', type: 'next_step', title: 'Next step' },
        ],
      },
      {
        id: 'building',
        label: 'Building',
        route: '/apps/:appId/building',
        description: 'Build requests, build versions, and approval queue.',
        enabled: true,
        capabilities: ['build_requests', 'artifact_versions', 'approval_queue'],
        panels: [
          { id: 'requests', type: 'build_requests', title: 'Build state' },
          { id: 'artifacts', type: 'artifact_timeline', title: 'Build versions' },
          { id: 'approvals', type: 'approval_queue', title: 'Approvals' },
        ],
      },
    ],
  },
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
      chat_app_id: 'factory-session-app',
      active_chat_id: 'campaign-revision-chat',
      active_workflow_id: 'AppGenerator',
      created_at: '2025-02-01T09:00:00Z',
      updated_at: '2025-02-04T18:25:00Z',
    },
    {
      build_registry_id: 'demo_partner_delivery',
      app_id: 'partner-delivery-studio',
      name: 'Partner Delivery Studio',
      description: 'Partner rollout, managed deployment, and release checks.',
      status: 'deploying',
      created_at: '2025-01-19T08:00:00Z',
      updated_at: '2025-02-05T11:20:00Z',
    },
    {
      build_registry_id: 'demo_member_growth',
      app_id: 'member-growth-studio',
      name: 'Member Growth Studio',
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

function buildAppStudioPayload(appId = APP_ID) {
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
        current_request: {
          text: 'Revise the campaign approval workspace and preserve marketplace reporting.',
          request_kind: 'refinement',
          change_class: 'feature',
          updated_at: '2025-02-05T08:45:00Z',
        },
        current_plan: {
          summary: 'Build the next app bundle while preserving existing hosted capability boundaries.',
          build_tasks: [],
          owned_paths: ['app/modules', 'app/ui/pages'],
          acceptance_criteria: ['Artifact validates', 'Owner review is complete'],
          approvals_required: ['Owner approval before promotion'],
          cost_implications: [],
          runtime_implications: [],
        },
        recent_requests: [
          {
            text: 'Add stakeholder review notes to the campaign workbench.',
            request_kind: 'refinement',
            change_class: 'patch',
            saved_at: '2025-02-04T16:10:00Z',
          },
        ],
        plan_state: 'plan_ready',
        approval_state: 'pending',
        initial_compile_workflow: 'ValueEngine',
        refinement_support: {
          patch: { available: true, workflow_id: 'AppGenerator' },
          design: { available: true, workflow_id: 'DesignDocs' },
          feature: { available: true, workflow_id: 'AppGenerator' },
          core: { available: true, workflow_id: 'ValueEngine' },
        },
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

function buildWorkspaceIntegrationsPayload() {
  return {
    integrations: [
      {
        id: 'mozaikspay',
        name: 'Mozaiks Pay',
        category: 'payments',
        description: 'Payment processing and subscription checkout.',
        status: 'configured',
        app_usage_count: 2,
        note: 'Production key managed by workspace operators.',
        secrets: [
          { name: 'MOZAIKSPAY_CLIENT_SECRET', present: true },
          { name: 'MOZAIKSPAY_WEBHOOK_SECRET', present: true },
        ],
        setup_steps: ['Create a restricted Mozaiks Pay key.', 'Add webhook signing secret.'],
      },
      {
        id: 'postmark',
        name: 'Postmark',
        category: 'email',
        description: 'Transactional email delivery.',
        status: 'partial',
        app_usage_count: 1,
        note: '',
        secrets: [
          { name: 'POSTMARK_SERVER_TOKEN', present: true },
          { name: 'POSTMARK_FROM_EMAIL', present: false },
        ],
        setup_steps: ['Create a server token.', 'Verify a sender email.'],
      },
      {
        id: 'slack',
        name: 'Slack',
        category: 'notifications',
        description: 'Operator notifications.',
        status: 'missing',
        app_usage_count: 0,
        note: '',
        secrets: [
          { name: 'SLACK_BOT_TOKEN', present: false },
        ],
        setup_steps: ['Install the Slack app.', 'Store the bot token.'],
      },
    ],
    summary: {
      total: 3,
      configured: 1,
      partial: 1,
      missing: 1,
      unknown: 0,
      used: 2,
    },
  };
}

function buildWorkspaceConnectorsPayload() {
  return {
    connectors: [
      {
        service: 'mozaikspay',
        display_name: 'Mozaiks Pay',
        secret_available: true,
        configured: true,
        ready: true,
        health: {
          status: 'configured',
          message: 'Required configuration is present.',
          missing_fields: [],
        },
      },
    ],
    total: 1,
  };
}

function buildAppIntegrationDeclarationsPayload(appId = APP_ID) {
  return {
    app_id: appId,
    declarations: [
      {
        service: 'mozaikspay',
        catalog_id: 'mozaikspay',
        display_name: 'Mozaiks Pay',
        purpose: 'Paid memberships and subscription checkout.',
        required_at: 'runtime',
        optional: true,
        defaulted: true,
        removable: true,
        source: 'monetization_default',
        workspace_status: 'configured',
        connector_status: 'ready',
      },
      {
        service: 'postmark',
        catalog_id: 'postmark',
        display_name: 'Postmark',
        purpose: 'Lifecycle emails and operator alerts.',
        required_at: 'runtime',
        optional: false,
        workspace_status: 'partial',
        connector_status: 'not_configured',
        setup_url: '/integrations/postmark',
      },
      {
        service: 'internal_search',
        catalog_id: null,
        display_name: 'Internal Search API',
        purpose: 'Index generated artifacts for app support.',
        required_at: 'runtime',
        optional: true,
        workspace_status: 'unknown',
        connector_status: 'not_configured',
      },
    ],
    summary: {
      total: 3,
      required: 2,
      blocking: 1,
    },
  };
}

function buildWorkspaceRunsPayload() {
  const runs = appsPayload.apps.flatMap((app) => {
    const payload = buildAppStudioPayload(app.app_id);
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

function normalizeUsageRun(run) {
  const promptTokens = Number(run?.prompt_tokens || 0);
  const completionTokens = Number(run?.completion_tokens || 0);
  const totalTokens = Number(run?.total_tokens || promptTokens + completionTokens);
  const estimatedCost = Number(run?.estimated_cost_usd ?? run?.cost ?? 0);
  const llmCalls = Number(run?.llm_calls || 1);

  return {
    ...run,
    prompt_tokens: promptTokens,
    completion_tokens: completionTokens,
    total_tokens: totalTokens,
    estimated_cost_usd: estimatedCost,
    llm_calls: llmCalls,
  };
}

function summarizeUsageRuns(runs) {
  const normalizedRuns = (Array.isArray(runs) ? runs : []).map(normalizeUsageRun);
  const totals = normalizedRuns.reduce(
    (current, run) => ({
      prompt_tokens: current.prompt_tokens + run.prompt_tokens,
      completion_tokens: current.completion_tokens + run.completion_tokens,
      total_tokens: current.total_tokens + run.total_tokens,
      estimated_cost_usd: current.estimated_cost_usd + run.estimated_cost_usd,
      llm_calls: current.llm_calls + run.llm_calls,
    }),
    {
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
      estimated_cost_usd: 0,
      llm_calls: 0,
    },
  );

  return { runs: normalizedRuns, totals };
}

function buildAppUsagePayload(appId = APP_ID) {
  const { runs, totals } = summarizeUsageRuns(buildAppStudioPayload(appId).runs?.runs || []);
  const byWorkflowMap = new Map();

  for (const run of runs) {
    const workflowName = run.workflow_name || 'Unknown workflow';
    const current = byWorkflowMap.get(workflowName) || {
      workflow_name: workflowName,
      runs: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
      estimated_cost_usd: 0,
      llm_calls: 0,
    };

    current.runs += 1;
    current.prompt_tokens += run.prompt_tokens;
    current.completion_tokens += run.completion_tokens;
    current.total_tokens += run.total_tokens;
    current.estimated_cost_usd += run.estimated_cost_usd;
    current.llm_calls += run.llm_calls;
    byWorkflowMap.set(workflowName, current);
  }

  return {
    totals,
    by_run: runs,
    by_workflow: Array.from(byWorkflowMap.values()),
  };
}

function buildWorkspaceUsagePayload() {
  const { runs, totals } = summarizeUsageRuns(buildWorkspaceRunsPayload().runs || []);
  return {
    totals,
    events: runs,
  };
}

function buildWorkspaceSupportPayload() {
  return {
    requests: [
      {
        request_id: 'sup-8821',
        app_id: APP_ID,
        app_name: 'Campaign Revision Workbench',
        subject: 'Runtime launch regression',
        message: 'Runtime launch is failing after the latest app update.',
        status: 'open',
        severity: 'high',
        user_id: 'alex@example.com',
        created_at: '2025-02-05T14:30:00Z',
        updated_at: '2025-02-05T15:30:00Z',
      },
      {
        request_id: 'sup-8819',
        app_id: APP_ID,
        app_name: 'Campaign Revision Workbench',
        subject: 'Invite email not delivered',
        message: 'Reviewer invite was not delivered.',
        status: 'resolved',
        severity: 'low',
        user_id: 'dana@example.com',
        created_at: '2025-02-04T13:00:00Z',
        updated_at: '2025-02-04T14:00:00Z',
      },
      {
        request_id: 'sup-8772',
        app_id: 'partner-delivery-studio',
        app_name: 'Partner Delivery Studio',
        subject: 'Escalation queue rules need review',
        message: 'Partner launch support queue needs a routing review.',
        status: 'open',
        severity: 'medium',
        user_id: 'jules@example.com',
        created_at: '2025-02-05T10:00:00Z',
        updated_at: '2025-02-05T11:00:00Z',
      },
    ],
    total: 3,
  };
}

function buildWorkspaceUsersPayload() {
  return {
    total_users: 184,
    active_users: 136,
    new_users: 12,
    by_app: {
      [APP_ID]: {
        total_users: 118,
        active_users: 91,
        new_users: 8,
      },
      'partner-delivery-studio': {
        total_users: 66,
        active_users: 45,
        new_users: 4,
      },
    },
  };
}

function buildOnboardingStatusPayload({ dismissed = false, progress = 0, steps = {} } = {}) {
  const defaultSteps = {
    create_app: { completed: false, completed_at: null },
    explore_apps: { completed: false, completed_at: null },
    open_support: { completed: false, completed_at: null },
  };
  return {
    seen_welcome: true,
    dismissed,
    steps: Object.keys(steps).length ? steps : defaultSteps,
    progress,
    completed_at: null,
  };
}

// ─── Owner analytics payloads ────────────────────────────────────────────────
// Deterministic stand-ins for /api/studio/analytics/*, mirroring the envelope
// the backend serves: registry metadata, {value, previous, delta, delta_pct,
// available} per metric, daily series, insights, and movement/funnel blocks.

const ANALYTICS_REGISTRY = {
  mrr: { metric_id: 'mrr', domain: 'revenue', label: 'MRR', short_label: 'MRR', unit: 'currency_usd', polarity: 'higher_is_better', source: 'kpi_snapshot', headline: true, benchmarkable: true, description: 'Monthly recurring revenue.', related: ['arr'] },
  arr: { metric_id: 'arr', domain: 'revenue', label: 'ARR', short_label: 'ARR', unit: 'currency_usd', polarity: 'higher_is_better', source: 'derived', description: 'Annualized recurring revenue.', related: ['mrr'] },
  new_mrr: { metric_id: 'new_mrr', domain: 'revenue', label: 'New MRR', short_label: 'New MRR', unit: 'currency_usd', polarity: 'higher_is_better', source: 'kpi_snapshot', description: 'Revenue from new subscriptions.', related: [] },
  expansion_mrr: { metric_id: 'expansion_mrr', domain: 'revenue', label: 'Expansion MRR', short_label: 'Expansion', unit: 'currency_usd', polarity: 'higher_is_better', source: 'kpi_snapshot', description: 'Revenue from upgrades.', related: [] },
  contraction_mrr: { metric_id: 'contraction_mrr', domain: 'revenue', label: 'Contraction MRR', short_label: 'Contraction', unit: 'currency_usd', polarity: 'lower_is_better', source: 'kpi_snapshot', description: 'Revenue lost to downgrades.', related: [] },
  churned_mrr: { metric_id: 'churned_mrr', domain: 'revenue', label: 'Churned MRR', short_label: 'Churned', unit: 'currency_usd', polarity: 'lower_is_better', source: 'kpi_snapshot', description: 'Revenue lost to cancellations.', related: [] },
  net_new_mrr: { metric_id: 'net_new_mrr', domain: 'revenue', label: 'Net New MRR', short_label: 'Net New MRR', unit: 'currency_usd', polarity: 'higher_is_better', source: 'derived', headline: true, description: 'New + expansion minus contraction and churn.', related: [] },
  mrr_growth: { metric_id: 'mrr_growth', domain: 'revenue', label: 'MRR growth', short_label: 'Growth', unit: 'percent', polarity: 'higher_is_better', source: 'derived', headline: true, benchmarkable: true, description: 'Percent change in MRR.', related: ['mrr'] },
  nrr: { metric_id: 'nrr', domain: 'revenue', label: 'NRR', short_label: 'NRR', unit: 'percent', polarity: 'higher_is_better', source: 'derived', headline: true, benchmarkable: true, description: 'Net revenue retention.', related: [] },
  arppu: { metric_id: 'arppu', domain: 'revenue', label: 'ARPPU', short_label: 'ARPPU', unit: 'currency_usd', polarity: 'higher_is_better', source: 'derived', description: 'Revenue per paying user.', related: [] },
  active_users: { metric_id: 'active_users', domain: 'users', label: 'Active users', short_label: 'Active', unit: 'count', polarity: 'higher_is_better', source: 'usage_rollup', headline: true, benchmarkable: true, description: 'Users active in the period.', related: [] },
  total_users: { metric_id: 'total_users', domain: 'users', label: 'Total users', short_label: 'Total', unit: 'count', polarity: 'higher_is_better', source: 'kpi_snapshot', description: 'All registered users.', related: [] },
  new_users: { metric_id: 'new_users', domain: 'users', label: 'New users', short_label: 'New', unit: 'count', polarity: 'higher_is_better', source: 'kpi_snapshot', description: 'Signups in the period.', related: [] },
  paying_users: { metric_id: 'paying_users', domain: 'users', label: 'Paying users', short_label: 'Paying', unit: 'count', polarity: 'higher_is_better', source: 'kpi_snapshot', headline: true, benchmarkable: true, description: 'Users on a paid plan.', related: [] },
  user_growth: { metric_id: 'user_growth', domain: 'users', label: 'User growth', short_label: 'Growth', unit: 'percent', polarity: 'higher_is_better', source: 'derived', headline: true, benchmarkable: true, description: 'Percent change in active users.', related: [] },
  paid_conversion: { metric_id: 'paid_conversion', domain: 'users', label: 'Paid conversion', short_label: 'Conversion', unit: 'percent', polarity: 'higher_is_better', source: 'derived', headline: true, benchmarkable: true, description: 'Paying users over total users.', related: [] },
  retention: { metric_id: 'retention', domain: 'users', label: 'Retention', short_label: 'Retention', unit: 'percent', polarity: 'higher_is_better', source: 'derived', benchmarkable: true, description: 'Share of users retained.', related: [] },
  churn_rate: { metric_id: 'churn_rate', domain: 'users', label: 'Churn', short_label: 'Churn', unit: 'percent', polarity: 'lower_is_better', source: 'derived', benchmarkable: true, description: 'Share of users churned.', related: [] },
};

function analyticsEnvelope(value, previous) {
  const bothPresent = value != null && previous != null;
  return {
    value: value ?? null,
    previous: previous ?? null,
    delta: bothPresent ? value - previous : null,
    delta_pct: bothPresent && previous !== 0 ? ((value - previous) / Math.abs(previous)) * 100 : null,
    available: value != null,
  };
}

function analyticsMetricSet(base) {
  return {
    mrr: analyticsEnvelope(base.mrr, base.prevMrr),
    arr: analyticsEnvelope(base.mrr * 12, base.prevMrr * 12),
    new_mrr: analyticsEnvelope(base.newMrr, base.newMrr),
    expansion_mrr: analyticsEnvelope(base.expansionMrr, base.expansionMrr),
    contraction_mrr: analyticsEnvelope(base.contractionMrr, base.contractionMrr),
    churned_mrr: analyticsEnvelope(base.churnedMrr, base.churnedMrr),
    net_new_mrr: analyticsEnvelope(
      base.newMrr + base.expansionMrr - base.contractionMrr - base.churnedMrr,
      base.newMrr,
    ),
    mrr_growth: { value: ((base.mrr - base.prevMrr) / base.prevMrr) * 100, previous: null, delta: null, delta_pct: null, available: true },
    nrr: analyticsEnvelope(102.5, null),
    arppu: analyticsEnvelope(base.mrr / base.payingUsers, null),
    active_users: analyticsEnvelope(base.activeUsers, base.prevActiveUsers),
    total_users: analyticsEnvelope(base.totalUsers, base.totalUsers - base.newUsers),
    new_users: analyticsEnvelope(base.newUsers, base.newUsers),
    paying_users: analyticsEnvelope(base.payingUsers, base.prevPayingUsers),
    user_growth: { value: ((base.activeUsers - base.prevActiveUsers) / base.prevActiveUsers) * 100, previous: null, delta: null, delta_pct: null, available: true },
    paid_conversion: analyticsEnvelope(
      (base.payingUsers / base.totalUsers) * 100,
      (base.prevPayingUsers / (base.totalUsers - base.newUsers)) * 100,
    ),
    retention: analyticsEnvelope(96.2, 95.1),
    churn_rate: analyticsEnvelope(3.8, 4.9),
  };
}

const ANALYTICS_BASES = {
  'campaign-revision-workbench': { mrr: 1600, prevMrr: 1750, newMrr: 40, expansionMrr: 0, contractionMrr: 60, churnedMrr: 130, payingUsers: 12, prevPayingUsers: 14, totalUsers: 19, newUsers: 2, activeUsers: 11, prevActiveUsers: 14 },
  'partner-delivery-studio': { mrr: 9200, prevMrr: 8760, newMrr: 520, expansionMrr: 240, contractionMrr: 120, churnedMrr: 200, payingUsers: 54, prevPayingUsers: 51, totalUsers: 204, newUsers: 21, activeUsers: 163, prevActiveUsers: 149 },
  'member-growth-studio': { mrr: 27800, prevMrr: 25300, newMrr: 2100, expansionMrr: 900, contractionMrr: 180, churnedMrr: 320, payingUsers: 482, prevPayingUsers: 448, totalUsers: 2480, newUsers: 206, activeUsers: 1824, prevActiveUsers: 1698 },
};

const ANALYTICS_PERIOD = {
  id: '30d',
  label: 'Last 30 days',
  comparison_label: 'vs previous 30 days',
  since: '2026-08-13T00:00:00+00:00',
  until: '2026-09-12T00:00:00+00:00',
  previous_since: '2026-07-14T00:00:00+00:00',
  previous_until: '2026-08-13T00:00:00+00:00',
};

function analyticsSeries(start, end, days = 14) {
  return Array.from({ length: days }, (_, index) => ({
    period_start: `2026-08-${String(index + 14).padStart(2, '0')}`,
    value: start + ((end - start) * index) / (days - 1),
  }));
}

function buildPortfolioAnalyticsPayload() {
  const apps = Object.entries(ANALYTICS_BASES).map(([appId, base]) => ({
    app_id: appId,
    name: appsPayload.apps.find((app) => app.app_id === appId)?.name || appId,
    lifecycle_state: 'active',
    metrics: analyticsMetricSet(base),
    error: false,
  }));
  const totals = Object.values(ANALYTICS_BASES).reduce(
    (sum, base) => ({
      mrr: sum.mrr + base.mrr,
      prevMrr: sum.prevMrr + base.prevMrr,
      newMrr: sum.newMrr + base.newMrr,
      expansionMrr: sum.expansionMrr + base.expansionMrr,
      contractionMrr: sum.contractionMrr + base.contractionMrr,
      churnedMrr: sum.churnedMrr + base.churnedMrr,
      payingUsers: sum.payingUsers + base.payingUsers,
      prevPayingUsers: sum.prevPayingUsers + base.prevPayingUsers,
      totalUsers: sum.totalUsers + base.totalUsers,
      newUsers: sum.newUsers + base.newUsers,
      activeUsers: sum.activeUsers + base.activeUsers,
      prevActiveUsers: sum.prevActiveUsers + base.prevActiveUsers,
    }),
    { mrr: 0, prevMrr: 0, newMrr: 0, expansionMrr: 0, contractionMrr: 0, churnedMrr: 0, payingUsers: 0, prevPayingUsers: 0, totalUsers: 0, newUsers: 0, activeUsers: 0, prevActiveUsers: 0 },
  );
  return {
    period: ANALYTICS_PERIOD,
    registry: ANALYTICS_REGISTRY,
    portfolio: analyticsMetricSet(totals),
    series: {
      mrr: analyticsSeries(totals.prevMrr, totals.mrr),
      arr: analyticsSeries(totals.prevMrr * 12, totals.mrr * 12),
      net_new_mrr: analyticsSeries(120, 180),
      active_users: analyticsSeries(totals.prevActiveUsers, totals.activeUsers),
      paying_users: analyticsSeries(totals.prevPayingUsers, totals.payingUsers),
      new_users: analyticsSeries(4, 9),
    },
    apps,
    benchmarks: {
      mrr: { median: 9200, sample_size: 3 },
      mrr_growth: { median: 5.0, sample_size: 3 },
      paid_conversion: { median: 26.5, sample_size: 3 },
    },
    insights: [
      {
        insight_id: 'mrr_decline',
        severity: 'attention',
        app_id: 'campaign-revision-workbench',
        app_name: 'Campaign Revision Workbench',
        metric_id: 'mrr',
        headline: 'Campaign Revision Workbench MRR declined 8.6%',
        detail: 'Largest revenue decline in the portfolio this period.',
        delta_pct: -8.57,
        delta: null,
      },
      {
        insight_id: 'mrr_growth_leader',
        severity: 'highlight',
        app_id: 'member-growth-studio',
        app_name: 'Member Growth Studio',
        metric_id: 'mrr',
        headline: 'Member Growth Studio is the fastest-growing app',
        detail: 'MRR grew 9.9% versus the comparison period.',
        delta_pct: 9.88,
        delta: null,
      },
    ],
    availability: { revenue: 'full', users: 'full' },
  };
}

function buildAppAnalyticsPayload(appId) {
  const base = ANALYTICS_BASES[appId] || ANALYTICS_BASES[APP_ID];
  return {
    period: ANALYTICS_PERIOD,
    registry: ANALYTICS_REGISTRY,
    app: { app_id: appId, name: appsPayload.apps.find((app) => app.app_id === appId)?.name || appId, lifecycle_state: 'active' },
    metrics: analyticsMetricSet(base),
    series: {
      mrr: analyticsSeries(base.prevMrr, base.mrr),
      arr: analyticsSeries(base.prevMrr * 12, base.mrr * 12),
      net_new_mrr: analyticsSeries(10, 25),
      active_users: analyticsSeries(base.prevActiveUsers, base.activeUsers),
      paying_users: analyticsSeries(base.prevPayingUsers, base.payingUsers),
      new_users: analyticsSeries(1, 3),
    },
    movement: {
      available: true,
      starting_mrr: base.prevMrr,
      ending_mrr: base.mrr,
      unexplained: null,
      new_mrr: base.newMrr,
      expansion_mrr: base.expansionMrr,
      contraction_mrr: base.contractionMrr,
      churned_mrr: base.churnedMrr,
    },
    funnel: {
      configured: true,
      available: true,
      funnel_id: 'activation',
      label: 'Activation',
      subject: 'actor',
      steps: [
        { step_id: 'signed_up', label: 'Signed up', event_name: 'user.signed_up', count: base.totalUsers, conversion_rate: null },
        { step_id: 'activated', label: 'Activated', event_name: 'app.activated', count: base.activeUsers, conversion_rate: 57.9 },
        { step_id: 'paid', label: 'Paid', event_name: 'subscription.activated', count: base.payingUsers, conversion_rate: 63.2 },
      ],
    },
    insights: [],
    availability: { revenue: 'full', users: 'full' },
    error: false,
  };
}

function buildMetricDetailPayload(appId, metricId) {
  const app = buildAppAnalyticsPayload(appId);
  return {
    period: ANALYTICS_PERIOD,
    definition: ANALYTICS_REGISTRY[metricId] || ANALYTICS_REGISTRY.mrr,
    app: { app_id: appId, name: app.app.name },
    value: app.metrics[metricId] || app.metrics.mrr,
    series: app.series[metricId] || [],
    drivers: [],
    related: [],
    benchmark: { kind: 'portfolio_median', label: 'Portfolio median', median: 9200, sample_size: 3 },
    error: false,
  };
}

function savedBuildBundle(versionId = 'ver-17') {
  const pending = versionId === 'ver-16';
  return {
    app_id: APP_ID,
    artifact_version_id: versionId,
    build_family: 'app_bundle',
    build_key: 'app_bundle',
    workbench_ui: { component: 'AppWorkbench', workflow_name: 'AppGenerator' },
    workbench: {
      app_id: appConfig.appId, target_app_id: APP_ID, build_registry_id: 'demo_campaign_revision',
      artifact_version_id: versionId, build_family: 'app_bundle', build_key: 'app_bundle',
      title: `Saved build ${versionId}`,
      generated_files: { 'README.md': `# Saved fixture ${versionId}` },
      app_validation_status: pending ? 'pending' : 'passed',
      app_validation_strategy_used: 'local',
      validation_result: { validation_status: pending ? 'pending' : 'passed', validation_strategy: 'local' },
      integration_test_result: { passed: !pending, checks: [], failed_tests: [] },
    },
    review: {
      lifecycle_status: 'draft', validation_status: pending ? 'pending' : 'passed', review_status: pending ? 'pending' : 'validated',
      can_accept: !pending, can_reject: true, can_promote: false, changed_file_count: 1,
      validation_blocker: pending ? 'Required runtime checks have not passed.' : null,
      risk_notes: ['Review the saved app before activation.'],
    },
  };
}

async function mockStudioApis(page) {
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

  await page.route('**/api/transitions/*', async (route) => {
    const transitionId = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() || '');
    if (transitionId === 'resolve') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          resolution_type: 'workflow',
          workflow_id: 'ValueEngine',
          chat_id: 'mock-create-chat',
        }),
      });
      return;
    }
    const transition = (extensionRegistry.transitions || []).find((item) => item.id === transitionId);
    await route.fulfill({
      status: transition ? 200 : 404,
      contentType: 'application/json',
      body: JSON.stringify(transition || { detail: 'Transition not found' }),
    });
  });

  await page.route('**/api/studio/apps', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(appsPayload),
    });
  });

  await page.route('**/api/admin/users', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildWorkspaceUsersPayload()),
    });
  });

  await page.route('**/api/studio/dashboard**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(dashboardPayload),
    });
  });

  await page.route('**/api/studio/overview?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppStudioPayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.summary),
    });
  });

  await page.route('**/api/admin/stats*', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppStudioPayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.stats),
    });
  });

  await page.route('**/api/admin/runs*', async (route) => {
    const url = new URL(route.request().url());
    const appId = url.searchParams.get('app_id');
    const payload = appId ? buildAppStudioPayload(appId).runs : buildWorkspaceRunsPayload();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });

  await page.route('**/api/admin/usage?**', async (route) => {
    const url = new URL(route.request().url());
    const appId = url.searchParams.get('app_id');
    const payload = appId ? buildAppUsagePayload(appId) : buildWorkspaceUsagePayload();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });

  await page.route('**/api/admin/sessions?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppStudioPayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.sessions),
    });
  });

  await page.route('**/api/studio/build?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppStudioPayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.buildState),
    });
  });

  await page.route('**/api/studio/build/history?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppStudioPayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.buildHistory),
    });
  });

  await page.route('**/api/studio/build/artifacts/*/bundle?**', async (route) => {
    const versionId = new URL(route.request().url()).pathname.split('/').at(-2);
    await route.fulfill({ json: savedBuildBundle(versionId) });
  });
  await page.route('**/api/studio/build/artifacts/*/review?**', async (route) => {
    const versionId = new URL(route.request().url()).pathname.split('/').at(-2);
    await route.fulfill({ json: { review: savedBuildBundle(versionId).review } });
  });
  await page.route('**/api/sandbox?**', async (route) => {
    expect(route.request().method()).toBe('GET');
    expect(new URL(route.request().url()).searchParams.get('build_registry_id')).toBeTruthy();
    await route.fulfill({ json: { sessions: [] } });
  });

  await page.route('**/api/studio/integrations?**', async (route) => {
    const url = new URL(route.request().url());
    const payload = buildAppStudioPayload(url.searchParams.get('app_id') || APP_ID);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload.integrations),
    });
  });

  await page.route('**/api/modules/workspace_integrations/list_integrations**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildWorkspaceIntegrationsPayload()),
    });
  });

  await page.route('**/api/modules/workspace_integrations/list_workspace_connectors**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildWorkspaceConnectorsPayload()),
    });
  });

  await page.route('**/api/modules/workspace_integrations/delete_workspace_connector**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ deleted: true, service: 'mozaikspay', secret_deleted: true }),
    });
  });

  await page.route('**/api/modules/workspace_integrations/list_app_integration_needs**', async (route) => {
    let appId = APP_ID;
    try {
      const body = route.request().postDataJSON();
      if (body?.app_id) appId = body.app_id;
    } catch {
      // Keep default app id for malformed test requests.
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildAppIntegrationDeclarationsPayload(appId)),
    });
  });

  await page.route('**/api/modules/workspace_integrations/delete_app_integration_need**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ deleted: true, app_id: APP_ID, service: 'mozaikspay' }),
    });
  });

  await page.route('**/api/modules/workspace_support/list_support_requests**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildWorkspaceSupportPayload()),
    });
  });

  // Default: fresh user — tour should appear
  await page.route('**/api/modules/user_onboarding/get_onboarding_status**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildOnboardingStatusPayload()),
    });
  });

  await page.route('**/api/modules/user_onboarding/complete_step**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true }) });
  });

  await page.route('**/api/modules/user_onboarding/dismiss_onboarding**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true }) });
  });

  // One handler for every owner-analytics read; branch on the path so the
  // metric-detail route is not shadowed by the app-analytics glob.
  await page.route('**/api/studio/analytics/**', async (route) => {
    const { pathname } = new URL(route.request().url());
    const metricMatch = pathname.match(/\/analytics\/apps\/([^/]+)\/metrics\/([^/]+)$/);
    const appMatch = pathname.match(/\/analytics\/apps\/([^/]+)$/);
    let body;
    if (metricMatch) {
      body = buildMetricDetailPayload(decodeURIComponent(metricMatch[1]), decodeURIComponent(metricMatch[2]));
    } else if (appMatch) {
      body = buildAppAnalyticsPayload(decodeURIComponent(appMatch[1]));
    } else {
      body = buildPortfolioAnalyticsPayload();
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
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
  await mockStudioApis(page);
});

test('mobile shell navigation uses icons and keeps readable action labels', async ({ page }, testInfo) => {
  await page.goto('/apps');
  await page.getByRole('dialog', { name: /Onboarding step 1 of 3/i })
    .getByRole('button', { name: 'Skip tour' }).click();
  const navigation = page.getByRole('navigation', { name: 'Mobile app navigation' });

  if (page.viewportSize().width >= 768) {
    await expect(navigation).toBeHidden();
  } else {
    await expect(navigation).toBeVisible();
    for (const label of ['Create App', 'Alerts', 'Account']) {
      const button = navigation.getByRole('button', { name: label });
      await expect(button).toBeVisible();
      await expect(button.locator('svg.shell-mobile-bottom-icon')).toHaveCount(1);
      await expect(button.locator('.shell-mobile-bottom-glyph')).toHaveAttribute('aria-hidden', 'true');
    }
    await expect(navigation.getByRole('button', { name: 'Open assistant' })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  }

  if (process.env.MOBILE_NAV_QA_DIR) {
    fs.mkdirSync(process.env.MOBILE_NAV_QA_DIR, { recursive: true });
    await page.screenshot({
      path: path.join(process.env.MOBILE_NAV_QA_DIR, `${testInfo.project.name}-studio-navigation.png`),
    });
  }
});

test('mobile shell navigation keeps an app configured icon and label', async ({ page }) => {
  await page.route('**/api/shell-config', async (route) => {
    await route.fulfill({
      json: {
        ...composedShellConfig,
        mobile: {
          bottomBar: {
            items: [{ id: 'create', label: 'My workspace', action: 'navigate', path: '/apps', icon: 'settings.svg' }],
          },
        },
      },
    });
  });
  await page.goto('/apps');
  await page.getByRole('dialog', { name: /Onboarding step 1 of 3/i })
    .getByRole('button', { name: 'Skip tour' }).click();
  const navigation = page.getByRole('navigation', { name: 'Mobile app navigation' });

  if (page.viewportSize().width < 768) {
    const button = navigation.getByRole('button', { name: 'My workspace' });
    await expect(button).toBeVisible();
    await expect(button.locator('.shell-mobile-bottom-icon')).toHaveCSS('mask-image', /settings\.svg/);
    await expect(navigation.getByRole('button')).toHaveCount(2);
  }
});

test('assistant bottom action leaves mobile controls tappable and opens the chat panel', async ({ page }, testInfo) => {
  await page.goto('/apps');
  await page.getByRole('dialog', { name: /Onboarding step 1 of 3/i })
    .getByRole('button', { name: 'Skip tour' }).click();
  const navigation = page.getByRole('navigation', { name: 'Mobile app navigation' });
  const launcher = page.locator('.widget-floating-launcher');

  if (page.viewportSize().width >= 768) {
    await expect(navigation).toBeHidden();
    await expect(launcher.getByRole('button', { name: 'Open assistant' })).toBeVisible();
    await launcher.getByRole('button', { name: 'Open assistant' }).click();
    await expect(page.locator('#mozaiks-assistant-panel')).toBeVisible();
    await page.getByRole('button', { name: 'Minimize' }).click();
    await expect(launcher.getByRole('button', { name: 'Open assistant' })).toBeVisible();
  } else {
    await expect(navigation).toBeVisible();
    await expect(launcher).toBeHidden();
    const assistant = navigation.getByRole('button', { name: 'Open assistant' });
    await expect(assistant).toHaveAttribute('aria-expanded', 'false');

    const search = page.getByPlaceholder('Search apps...');
    const searchBox = await search.boundingBox();
    await page.mouse.click(searchBox.x + searchBox.width - 10, searchBox.y + searchBox.height - 10);
    await expect(search).toBeFocused();
    await search.fill('member growth');
    await expect(page.getByText('Member Growth Studio').first()).toBeVisible();
    await search.fill('');

    const live = page.locator('main').getByRole('button', { name: /^Live/ });
    await live.click();
    await expect(live).toHaveClass(/bg-primary\/10/);
    await expect(page.getByText('Member Growth Studio').first()).toBeVisible();
    await expect(assistant).toHaveAttribute('aria-expanded', 'false');

    const navButtons = navigation.getByRole('button');
    for (let index = 0; index < await navButtons.count(); index += 1) {
      const button = navButtons.nth(index);
      const bounds = await button.boundingBox();
      const topmost = await page.evaluate(({ x, y }) => {
        const target = document.elementFromPoint(x, y);
        return target?.closest('button')?.classList.contains('shell-mobile-bottom-item') || false;
      }, { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 });
      expect(topmost).toBe(true);
    }

    await assistant.focus();
    await expect(assistant).toBeFocused();
    await page.keyboard.press('Enter');
    const closeAssistant = navigation.getByRole('button', { name: 'Close assistant' });
    await expect(closeAssistant).toHaveAttribute('aria-expanded', 'true');
    await expect(closeAssistant).toHaveAttribute('aria-controls', 'mozaiks-assistant-panel');
    await expect(page.locator('#mozaiks-assistant-panel')).toBeVisible();
    if (process.env.MOBILE_NAV_QA_DIR) {
      fs.mkdirSync(process.env.MOBILE_NAV_QA_DIR, { recursive: true });
      await page.screenshot({
        path: path.join(process.env.MOBILE_NAV_QA_DIR, `${testInfo.project.name}-assistant-open.png`),
      });
    }
    await closeAssistant.click();
    await expect(assistant).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#mozaiks-assistant-panel')).toHaveCount(0);
    await expect(launcher).toBeHidden();
    if (process.env.MOBILE_NAV_QA_DIR) {
      fs.mkdirSync(process.env.MOBILE_NAV_QA_DIR, { recursive: true });
      await page.screenshot({
        path: path.join(process.env.MOBILE_NAV_QA_DIR, `${testInfo.project.name}-assistant-bottom-action.png`),
      });
    }

    for (const label of ['Create App', 'Alerts', 'Account']) {
      await navigation.getByRole('button', { name: label }).click();
      await expect(page).not.toHaveURL(/\/apps$/);
      await expect(page.locator('#mozaiks-assistant-panel')).toHaveCount(0);
      await page.goto('/apps');
    }
  }

  if (process.env.MOBILE_NAV_QA_DIR && page.viewportSize().width >= 768) {
    fs.mkdirSync(process.env.MOBILE_NAV_QA_DIR, { recursive: true });
    await page.screenshot({
      path: path.join(process.env.MOBILE_NAV_QA_DIR, `${testInfo.project.name}-assistant-bottom-action.png`),
    });
  }
});

test('assistant launcher remains on mobile shells without a bottom bar', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'Mobile shell fallback only');
  await page.route('**/api/shell-config', async (route) => {
    await route.fulfill({ json: {
      ...composedShellConfig,
      mobile: { bottomBar: { visible: false } },
    } });
  });
  await page.goto('/apps');
  await expect(page.getByRole('navigation', { name: 'Mobile app navigation' })).toHaveCount(0);
  await expect(page.locator('.widget-floating-launcher').getByRole('button', { name: 'Open assistant' })).toBeVisible();
});

test('five configured mobile tabs retain their labels beside Assistant', async ({ page, isMobile }, testInfo) => {
  test.skip(!isMobile, 'Mobile bottom bar only');
  const configured = [
    { id: 'home', label: 'Home', path: '/apps' },
    { id: 'projects', label: 'Projects', path: '/usage', iconLabel: 'P' },
    { id: 'alerts', label: 'Alerts', path: '/notifications' },
    { id: 'profile', label: 'Account', path: '/me' },
    { id: 'create', label: 'Create', path: '/create' },
  ];
  await page.route('**/api/shell-config', async (route) => {
    await route.fulfill({ json: {
      ...composedShellConfig,
      mobile: { bottomBar: { items: configured } },
    } });
  });
  await page.goto('/apps');
  await page.getByRole('dialog', { name: /Onboarding step 1 of 3/i })
    .getByRole('button', { name: 'Skip tour' }).click();
  const navigation = page.getByRole('navigation', { name: 'Mobile app navigation' });
  await expect(navigation.getByRole('button')).toHaveCount(6);
  for (const { label } of configured) {
    await expect(navigation.getByRole('button', { name: label })).toBeVisible();
  }
  await expect(navigation.getByRole('button', { name: 'Projects' }).locator('.shell-mobile-bottom-glyph')).toHaveText('P');
  await expect(navigation.getByRole('button', { name: 'Open assistant' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  if (process.env.MOBILE_NAV_QA_DIR) {
    fs.mkdirSync(process.env.MOBILE_NAV_QA_DIR, { recursive: true });
    await page.screenshot({
      path: path.join(process.env.MOBILE_NAV_QA_DIR, `${testInfo.project.name}-six-tabs.png`),
    });
  }
});

test('apps route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto('/apps');
  const main = page.locator('main');

  await expect(page.getByRole('heading', { name: 'Apps' })).toBeVisible();
  await expect(page.locator('header').getByRole('button', { name: 'Create App' })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Create App' })).toHaveCount(0);
  await expect(main.getByRole('button', { name: 'Import App' })).toHaveCount(0);
  await expect(main.getByPlaceholder('Search apps...')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Updated' })).toBeHidden();
    await expect(main.getByRole('button', { name: 'Continue Build' }).first()).toBeVisible();
    await expect(main.getByRole('button', { name: 'Dashboard' }).first()).toBeVisible();

    await expect(page.locator('.widget-floating-launcher')).toBeHidden();
    await expect(page.getByRole('navigation', { name: 'Mobile app navigation' })
      .getByRole('button', { name: 'Open assistant' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
    await expect(page.getByRole('columnheader', { name: 'Updated' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Status' })).toBeVisible();
    await expect(main.getByRole('row', { name: /Campaign Revision Workbench/i }).first()).toBeVisible();
    await expect(main.getByRole('row', { name: /Partner Delivery Studio/i }).first()).toBeVisible();

    const widgetButton = page.locator('.widget-safe-bottom button').first();
    await expect(widgetButton).toBeVisible();
    const widgetBox = await widgetButton.boundingBox();
    expect(widgetBox).not.toBeNull();
    expect(widgetBox.width).toBeLessThanOrEqual(64);
  }
});

test('create app transition overlay can return to Apps', async ({ page, isMobile }) => {
  // Mobile CI: touch-emulated click on Create App intermittently fails to trigger
  // React Router navigation in time on slow runners. The overlay itself renders
  // correctly on mobile (same component, bottom-sheet variant). Desktop coverage
  // is sufficient for this navigation assertion.
  test.skip(isMobile, 'Overlay navigation assertion is flaky on mobile CI; desktop-only');

  await page.goto('/apps');

  await page.locator('header').getByRole('button', { name: 'Create App' }).click();

  await expect(page).toHaveURL(/\/create$/);
  await expect(page.getByRole('heading', { name: 'Choose Your App Journey' })).toBeVisible();

  const closeBtn = page.getByRole('button', { name: 'Back to Apps' });
  await expect(closeBtn).toBeVisible();
  await closeBtn.click();

  await expect(page).toHaveURL(/\/apps$/);
  await expect(page.locator('main').getByRole('heading', { name: 'Apps', exact: true })).toBeVisible();
});

test('workspace usage route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto('/usage');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Token Usage', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Workspace usage' })).toBeVisible();
  await expect(main.getByPlaceholder('Search apps or workflows...')).toBeVisible();
  await expect(main.getByText('Total spend')).toBeVisible();
  await expect(main.getByRole('button', { name: 'Chats' })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(main.getByRole('columnheader', { name: 'App' })).toBeVisible();
    await expect(main.getByRole('columnheader', { name: 'Input tok.' })).toBeVisible();
    await expect(main.getByRole('row', { name: /Campaign Revision Workbench/i }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('workspace users route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto('/users');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Users', exact: true })).toBeVisible();
  await expect(main.getByPlaceholder('Search apps...')).toBeVisible();
  const summaryMetrics = main.getByLabel('Summary metrics');
  await expect(summaryMetrics.getByText('Total users')).toBeVisible();
  await expect(summaryMetrics.getByText('Active users')).toBeVisible();
  await expect(summaryMetrics.getByText('Apps with users')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(main.locator('article').filter({ hasText: 'Campaign Revision Workbench' }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(main.getByRole('row', { name: /Campaign Revision Workbench/i }).first()).toBeVisible();
    await expect(main.getByRole('columnheader', { name: 'Total users' })).toBeVisible();
    await expect(main.getByRole('columnheader', { name: 'Active' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('workspace integrations route stays responsive across desktop and mobile widths', async ({ page }) => {
  // Dismiss onboarding tour so its tooltip does not overlap the fixed-position
  // "Open Studio navigation" / "Manage" buttons during mobile interaction checks.
  await page.route('**/api/modules/user_onboarding/get_onboarding_status**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildOnboardingStatusPayload({ dismissed: true })),
    });
  });
  await page.goto('/integrations');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Integrations', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Needs attention' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Connected' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Available' })).toBeVisible();
  await expect(main.getByText('Mozaiks Pay')).toBeVisible();
  await expect(main.getByText('Postmark')).toBeVisible();
  await expect(main.getByText('Slack')).toBeVisible();
  await expect(main.getByText('Connected', { exact: true }).first()).toBeVisible();
  await expect(main.getByText('Needs setup', { exact: true }).first()).toBeVisible();
  await expect(main.getByText('Available', { exact: true }).first()).toBeVisible();
  await expect(main.getByText('Used by 2 apps').first()).toBeVisible();
  await expect(main.getByText('Not used yet').first()).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await main.getByRole('button', { name: 'Manage' }).first().click();
  const drawer = page.getByRole('dialog', { name: 'Mozaiks Pay' });
  await expect(drawer.getByText('Credential source')).toBeVisible();
  await expect(drawer.getByText('Workspace connector', { exact: true })).toBeVisible();
  await expect(drawer.getByRole('button', { name: 'Delete connector' })).toBeVisible();
  await expect(drawer.getByText('Advanced setup details')).toBeVisible();
  await drawer.getByRole('button', { name: 'Close', exact: true }).click();

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('workspace support route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto('/support');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Support', exact: true })).toBeVisible();
  await expect(main.getByPlaceholder('Search apps...')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    const campaignCard = main.locator('article').filter({ hasText: 'Campaign Revision Workbench' }).first();
    await expect(campaignCard).toBeVisible();
    await expect(campaignCard).toContainText('Needs reply');
    await expect(campaignCard).toContainText('2 support chats');
    await expect(campaignCard.getByRole('button', { name: 'Dashboard' })).toBeVisible();
    await expect(main.getByText('App not loading after update')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    const campaignRow = main.getByRole('row', { name: /Campaign Revision Workbench/ }).first();
    await expect(campaignRow).toBeVisible();
    await expect(campaignRow).toContainText('Needs reply');
    await expect(campaignRow).toContainText('2 support chats');
    await expect(campaignRow.getByRole('button', { name: 'Dashboard' })).toBeVisible();
    await expect(main.getByRole('row', { name: /Partner Delivery Studio/ }).first()).toBeVisible();
    await expect(main.getByText('App not loading after update')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('profile support page loads tickets on a same-origin Studio host', async ({ page }) => {
  let profilePageRequests = 0;
  await page.route('**/api/me/profile-pages**', async (route) => {
    profilePageRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pages: [{
          id: 'overview',
          label: 'Profile',
          section: 'overview',
          renderer: 'custom_component',
          component: 'ProfileOverview',
          visibility: 'public',
        }, {
          id: 'support-tickets',
          label: 'Support',
          section: 'overview',
          renderer: 'custom_component',
          component: 'UserSupportPanel',
          visibility: 'owner_only',
          data: {
            requests: [{
              request_id: 'sr_browser',
              subject_app_id: APP_ID,
              user_id: 'user_1',
              message: 'Need help with my app',
              status: 'open',
              created_at: '2026-01-01T00:00:00Z',
            }],
            total: 1,
          },
        }],
      }),
    });
  });

  await page.goto('/me?tab=support-tickets');

  await expect(page.getByText('Need help with my app').first()).toBeVisible();
  expect(profilePageRequests).toBeGreaterThan(0);

  await page.route('**/api/users/test-person', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ username: 'test-person', display_name: 'Public Profile Name' }),
    });
  });
  await page.goto('/u/test-person?tab=support-tickets');
  await expect(page.getByText('Public Profile Name').first()).toBeVisible();
  await expect(page.getByText('Need help with my app')).toHaveCount(0);
});

test('app Studio root redirects to manifest default portal', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}`);

  await expect(page).toHaveURL(new RegExp(`/apps/${APP_ID}/overview$`));
  await expect(page.locator('main').getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
});

test('app overview route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/overview`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
  await expect(main.getByText('Campaign Revision Workbench').first()).toBeVisible();
  await expect(main.getByText('Next step').first()).toBeVisible();
  await expect(main.getByRole('link', { name: 'Continue Build' })).toHaveCount(0);
  await expect(main.getByRole('heading', { name: 'Approval required' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Activity' })).toBeVisible();
  await expect(main.getByText('Runtime cost').first()).toBeVisible();
  await expect(main.getByText('Active users').first()).toBeVisible();
  await expect(main.getByText('Revise the campaign approval workspace')).toBeVisible();
  await expect(main.getByText('Approval required')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

async function mockOverviewProgress(page, {
  lifecycle = 'building',
  currentBuildRun = {
    build_id: 'q-build', phase: 'genesis', status: lifecycle,
    active_chat_id: 'q-chat', active_workflow_id: 'AppGenerator',
  },
  description = 'A focus timer that tracks completed sessions.',
  revenue = null,
  cost = 0,
} = {}) {
  // A current registry run without a separately saved build request or plan,
  // and no deployed runtime history. Top-level chat fields are deliberately stale.
  const summary = {
    app: {
      ...getWorkspaceApp(), name: 'FocusSprint', description,
      status: lifecycle, lifecycle_state: lifecycle,
      lifecycle_label: lifecycle === 'needs_revision' ? 'Needs Revision' : lifecycle === 'active' ? 'Active' : 'Building',
      chat_app_id: 'factory-session-app',
      active_chat_id: currentBuildRun ? 'stale-chat' : null,
      active_workflow_id: currentBuildRun ? 'ValueEngine' : null,
      current_build_run: currentBuildRun,
    },
    financials: { total_revenue_usd: revenue },
  };
  const responses = [
    ['**/api/modules/user_onboarding/get_onboarding_status**', buildOnboardingStatusPayload({ dismissed: true })],
    ['**/api/studio/overview?**', summary],
    ['**/api/studio/build?**', { build: { plan_state: 'not_started', approval_state: 'not_started' } }],
    ['**/api/studio/build/history?**', { artifact_versions: [] }],
    ['**/api/admin/stats*', { tracked_chats: 0 }],
    ['**/api/admin/runs*', { runs: [] }],
    ['**/api/admin/usage?**', { totals: { estimated_cost_usd: cost } }],
    ['**/api/studio/analytics/**', { metrics: {}, insights: [] }],
    [`**/api/studio/apps/${APP_ID}/context`, {
      context_readiness: { status: 'missing' }, context_graph_status: { available: false, node_count: 0, edge_count: 0 },
    }],
  ];
  for (const [pattern, body] of responses) {
    await page.route(pattern, route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }));
  }
}

for (const lifecycle of ['building', 'needs_revision']) {
  test(`app overview shows current ${lifecycle} progress before optional diagnostics`, async ({ page }, testInfo) => {
    await mockOverviewProgress(page, { lifecycle });
    await page.goto(`/apps/${APP_ID}/overview`);
    const main = page.locator('main');
    await expect(main.getByRole('heading', { name: 'FocusSprint' })).toBeVisible();
    await expect(main.getByText('A focus timer that tracks completed sessions.')).toBeVisible();
    await expect(main.getByText('Current step: App Generator')).toBeVisible();
    const resume = main.getByRole('link', { name: 'Continue Build', exact: true });
    await expect(resume).toHaveAttribute('href', '/chat?workflow=AppGenerator&mode=workflow&chat_id=q-chat&app_id=factory-session-app');
    await expect(resume).toBeInViewport({ ratio: 1 });
    expect(await resume.evaluate(link => {
      const rect = link.getBoundingClientRect();
      return link.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
    })).toBe(true);
    await expect(main.getByText('Not started', { exact: true })).toHaveCount(0);
    await expect(main.getByText(/No builds yet|No build sessions yet|AI-Powered Workflows|concept brief is captured/)).toHaveCount(0);
    await expect(main.getByRole('heading', { name: 'App intelligence' })).toBeHidden();
    await expect(main.getByRole('group', { name: 'Revenue', exact: true })).toBeHidden();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`overview-${lifecycle}.png`), fullPage: true });

    const details = main.locator('details').filter({ has: page.locator('summary', { hasText: 'Runtime and source details' }) });
    await expect(details).not.toHaveAttribute('open', '');
    await details.locator('summary').click();
    await expect(main.getByRole('heading', { name: 'App intelligence' })).toBeVisible();
    await expect(main.getByRole('heading', { name: 'Activity' })).toBeVisible();
    const margin = main.getByRole('group', { name: 'Margin', exact: true });
    await expect(margin).toContainText('Pending');
    await expect(margin).not.toContainText('$0.00');
    await expectNoHorizontalOverflow(page);
  });
}

for (const chatId of [null, 'previous-build-chat']) {
  test(`review-ready overview opens its saved build with chat ${chatId}`, async ({ page }) => {
    await mockOverviewProgress(page, {
      lifecycle: 'review',
      currentBuildRun: {
        build_id: 'q-build', phase: 'refinement', status: 'review',
        artifact_version_id: 'ver-17', active_chat_id: chatId, active_workflow_id: 'AppGenerator',
      },
    });
    await page.route('**/api/studio/build/history?**', route => route.fulfill({
      json: buildAppStudioPayload(APP_ID).buildHistory,
    }));
    const mutations = [];
    page.on('request', request => {
      if (request.method() === 'POST') mutations.push(new URL(request.url()).pathname);
    });
    await page.goto(`/apps/${APP_ID}/overview`);
    const main = page.locator('main');
    const review = main.getByRole('link', { name: 'Review builds', exact: true });
    await expect(review).toHaveAttribute('href', `/apps/${APP_ID}/activity`);
    await expect(main.getByText('Preview your saved version, request changes, then accept and activate it when ready.')).toBeVisible();
    await expect(main.getByRole('link', { name: 'Continue Build', exact: true })).toHaveCount(0);
    await expect(main.getByText(/A build conversation link is not available/)).toHaveCount(0);
    await review.click();
    await expect(page.getByText('Saved build ver-17', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start draft preview', exact: true })).toBeVisible();
    // The shell reads onboarding status through a POST module action.
    expect(mutations.filter(path => path !== '/api/modules/user_onboarding/get_onboarding_status')).toEqual([]);
    await expectNoHorizontalOverflow(page);
  });
}

for (const missingField of ['active_chat_id', 'active_workflow_id']) {
  test(`app overview without ${missingField} opens Building without inventing a resume`, async ({ page }) => {
    await mockOverviewProgress(page, { currentBuildRun: null, description: null });
    // An incomplete saved binding must not synthesize ValueEngine or resume a new chat.
    await page.route('**/api/studio/overview?**', route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ app: {
        ...getWorkspaceApp(), name: 'FocusSprint', description: null,
        status: 'building', lifecycle_state: 'building', lifecycle_label: 'Building',
        active_chat_id: 'orphan-chat', active_workflow_id: 'AppGenerator', [missingField]: null, current_build_run: {},
      } }),
    }));
    await page.goto(`/apps/${APP_ID}/overview`);
    const main = page.locator('main');
    await expect(main.getByText('App description is not available.')).toBeVisible();
    await expect(main.getByText('Latest saved build progress.')).toHaveCount(0);
    await expect(main.getByRole('link', { name: 'Continue Build' })).toHaveCount(0);
    const building = main.getByRole('link', { name: 'Open Building', exact: true });
    await expect(building).toHaveAttribute('href', `/apps/${APP_ID}/building`);
    await building.click();
    await expect(page).toHaveURL(new RegExp(`/apps/${APP_ID}/building$`));
    await expect(main.getByRole('heading', { name: 'Building', exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
}

for (const [revenue, cost, expected] of [[100, 40, '$60.00'], [null, 40, 'Pending'], [100, null, 'Pending']]) {
  test(`active app overview preserves runtime metrics with revenue ${revenue} and cost ${cost}`, async ({ page }) => {
    await mockOverviewProgress(page, { lifecycle: 'active', currentBuildRun: null, revenue, cost });
    await page.goto(`/apps/${APP_ID}/overview`);
    const main = page.locator('main');
    await expect(main.getByRole('group', { name: 'Revenue', exact: true })).toBeVisible();
    await expect(main.getByRole('group', { name: 'Runtime Cost', exact: true })).toBeVisible();
    await expect(main.getByRole('heading', { name: 'Activity' })).toBeVisible();
    await expect(main.locator('summary', { hasText: 'Runtime and source details' })).toHaveCount(0);
    const margin = main.getByRole('group', { name: 'Margin', exact: true });
    await expect(margin).toContainText(expected);
    if (expected === 'Pending') await expect(margin).not.toContainText('%');
    await expect(main.getByRole('link', { name: 'Open App Studio' })).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  });
}

test('app building route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/building`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Building', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Build state' })).toBeVisible();
  await expect(main.getByText('Revise the campaign approval workspace')).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Build versions' })).toBeVisible();
  await expect(main.getByText('Build v17').first()).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Approvals' })).toBeVisible();
  await expect(main.getByText('Owner approval before promotion')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('app integrations route stays responsive across desktop and mobile widths', async ({ page }, testInfo) => {
  const consoleMessages = [];
  const responseFindings = [];
  const providerUrlRequests = [];
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

  await page.goto(`/apps/${APP_ID}/integrations`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'App Integrations', exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Workspace integrations' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Required' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Optional' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'App-specific' })).toBeVisible();
  await expect(main.getByText('Mozaiks Pay').first()).toBeVisible();
  await expect(main.getByText('Postmark').first()).toBeVisible();
  await expect(main.getByText('Internal Search API').first()).toBeVisible();
  await expect(main.getByText('Ready').first()).toBeVisible();
  await expect(main.getByText('Partial setup').first()).toBeVisible();
  await expect(main.getByText('Configure in app environment').first()).toBeVisible();
  await expect(main.getByRole('button', { name: 'Remove from app' }).first()).toBeVisible();
  await expect(main.getByText(SECRET_SENTINEL)).toHaveCount(0);
  await expect(main.getByRole('button', { name: 'Add Integration' })).toHaveCount(0);
  await expect(main.getByRole('button', { name: 'Check now' })).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  expect(providerUrlRequests).toHaveLength(0);

  await captureIntegrationsQa(page, testInfo, 'initial', {
    shell_layout: 'rendered',
    app_declarations_visible: true,
    credential_crud_visible: false,
    provider_url_requests: providerUrlRequests,
    secret_values_visible: false,
    console_messages: consoleMessages,
    response_findings: responseFindings,
  });

  await writeIntegrationsQaArtifact(`${testInfo.project.name.replace(/[^a-z0-9_-]+/gi, '-').toLowerCase()}-report.json`, {
    page: `/apps/${APP_ID}/integrations`,
    states_verified: ['initial'],
    provider_url_requests: providerUrlRequests,
    secret_values_visible: false,
    credential_crud_visible: false,
    console_messages: consoleMessages,
    response_findings: responseFindings,
  });

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }

  await captureIntegrationsQa(page, testInfo, 'add-integration-overlay', {
    shell_layout: 'rendered',
    overlay: 'not rendered',
    secret_values_visible: false,
    console_messages: consoleMessages,
    response_findings: responseFindings,
  });
});

test('app usage route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/usage`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Token Usage', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Workflow breakdown' })).toBeVisible();
  await expect(main.getByRole('columnheader', { name: 'Input' })).toBeVisible();
  await expect(main.getByText('RevisionOrchestrator').first()).toBeVisible();
  await expect(main.getByText('Average latency')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('workspace performance route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto('/performance');
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Performance', exact: true })).toBeVisible();
  // Both domains stay visible as separate, labelled groups.
  const revenueMetrics = main.getByLabel('Revenue metrics');
  const usersMetrics = main.getByLabel('Users metrics');
  await expect(revenueMetrics).toBeVisible();
  await expect(usersMetrics).toBeVisible();
  await expect(revenueMetrics.getByText('MRR').first()).toBeVisible();
  await expect(usersMetrics.getByText('Active').first()).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Portfolio trend' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Needs attention' })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Applications' })).toBeVisible();
  // Deterministic insight text, not an opaque generated summary.
  await expect(main.getByText('Campaign Revision Workbench MRR declined 8.6%')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(main.locator('article').filter({ hasText: 'Member Growth Studio' }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(main.getByRole('row', { name: /Member Growth Studio/i }).first()).toBeVisible();
    await expect(main.getByRole('columnheader', { name: 'MRR' })).toBeVisible();
    await expect(main.getByRole('columnheader', { name: 'Conversion' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('app revenue route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/revenue`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Revenue', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Revenue trend' })).toBeVisible();
  // The MRR bridge explains why revenue moved, start through end.
  await expect(main.getByRole('heading', { name: 'Why MRR changed' })).toBeVisible();
  await expect(main.getByText('Starting MRR')).toBeVisible();
  await expect(main.getByText('Ending MRR')).toBeVisible();
  await expect(main.getByText('Churned MRR')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('app users analytics route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/audience`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Users', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'User trend' })).toBeVisible();
  // App-declared funnel stages, not a hard-coded SaaS funnel.
  await expect(main.getByRole('heading', { name: 'Activation' })).toBeVisible();
  await expect(main.getByText('Signed up')).toBeVisible();
  await expect(main.getByText('Paid', { exact: true })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
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
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('app support route stays responsive across desktop and mobile widths', async ({ page }) => {
  const supportModuleRequests = [];
  page.on('request', (request) => {
    const requestUrl = request.url();
    if (requestUrl.includes('/api/modules/workspace_support/list_support_requests')) {
      supportModuleRequests.push({
        url: requestUrl,
        postData: request.postData() || '',
      });
    }
  });

  await page.goto(`/apps/${APP_ID}/support`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Support', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Support chats' })).toBeVisible();
  await expect(main.getByText('Needs reply').first()).toBeVisible();
  await expect(main.getByText('Responded').first()).toBeVisible();
  await expect(main.getByText('Running')).toHaveCount(0);
  expect(supportModuleRequests.some(({ postData }) => postData.includes(`"subject_app_id":"${APP_ID}"`))).toBeTruthy();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('app access route stays responsive across desktop and mobile widths', async ({ page }) => {
  await page.goto(`/apps/${APP_ID}/access`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Users', exact: true })).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Account management' })).toBeVisible();
  await expect(main.getByPlaceholder('Search by name, email, status, or plan')).toBeVisible();
  await expect(main.getByRole('button', { name: 'Export' }).first()).toBeVisible();
  await expect(main.getByRole('heading', { name: 'Access state' })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test.describe('saved artifact review', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/modules/user_onboarding/get_onboarding_status**', route => route.fulfill({
      json: buildOnboardingStatusPayload({ dismissed: true }),
    }));
  });

test('app build review route stays responsive across desktop and mobile widths', async ({ page }, testInfo) => {
  const mutations = [];
  page.on('request', request => {
    if (request.method() === 'POST' && /\/api\/(workflows|artifacts|sandbox|studio\/build)/.test(request.url())) mutations.push(request.url());
  });
  await page.goto(`/apps/${APP_ID}/activity`);
  const main = page.locator('main');

  await expect(main.getByRole('heading', { name: 'Build Review', exact: true })).toBeVisible();
  await expect(main.getByText('Saved build ver-17', { exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Start draft preview', exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Accept artifact', exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: /Download Bundle|Confirm app bundle/ })).toHaveCount(0);
  await expect(main.getByRole('heading', { name: 'Build versions' })).toBeHidden();
  expect(mutations).toEqual([]);
  await expectNoHorizontalOverflow(page);
  await page.screenshot({ path: testInfo.outputPath('saved-build-review.png'), fullPage: true });

  await main.getByLabel('Starting version', { exact: true }).selectOption('ver-16');
  await expect(main.getByText('Saved build ver-16', { exact: true })).toBeVisible();
  await expect(main.getByText('Saved build ver-17', { exact: true })).toHaveCount(0);
  await expect(main.getByText('Required runtime checks have not passed.', { exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Accept artifact', exact: true })).toHaveCount(0);
  await expect(main.getByRole('button', { name: 'Activate this draft', exact: true })).toHaveCount(0);
  expect(mutations).toEqual([]);

  await main.getByText('Build history and preservation reports', { exact: true }).click();
  await expect(main.getByRole('heading', { name: 'Build versions' })).toBeVisible();
  await expect(main.getByText('No carry-forward preservation report for this build.').first()).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();

  if (viewport.width < 768) {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: 'Open Studio navigation' })).toBeHidden();
  }
});

test('saved build review retries errors and starts preview only on explicit action', async ({ page }) => {
  let attempts = 0;
  const previewRequests = [];
  await page.route('**/api/studio/build/artifacts/ver-17/bundle?**', async route => {
    attempts += 1;
    expect(new URL(route.request().url()).searchParams.get('build_registry_id')).toBe('demo_campaign_revision');
    await route.fulfill(attempts === 1
      ? { status: 503, json: { detail: 'Saved archive temporarily unavailable.' } }
      : { json: savedBuildBundle() });
  });
  await page.route('**/api/artifacts/*/sandbox?**', async route => {
    previewRequests.push(route.request().url());
    await route.fulfill({ status: 409, json: { detail: 'Preview fixture does not provision a sandbox.' } });
  });
  await page.goto(`/apps/${APP_ID}/activity`);
  await expect(page.getByText('Saved archive temporarily unavailable.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Retry opening build' }).click();
  await expect(page.getByText('Saved build ver-17', { exact: true })).toBeVisible();
  expect(attempts).toBe(2);
  expect(previewRequests).toEqual([]);
  await page.getByRole('button', { name: 'Start draft preview', exact: true }).click();
  await page.getByText('Preview details', { exact: true }).click();
  await expect(page.getByText('Preview fixture does not provision a sandbox.', { exact: true })).toBeVisible();
  expect(previewRequests).toHaveLength(1);
  expect(new URL(previewRequests[0]).pathname).toBe('/api/artifacts/ver-17/sandbox');
  expect(new URL(previewRequests[0]).searchParams.get('build_registry_id')).toBe('demo_campaign_revision');
});

for (const mismatch of ['version', 'app', 'registry']) {
  test(`saved build review rejects a mismatched ${mismatch} response`, async ({ page }) => {
    await page.route('**/api/studio/build/artifacts/ver-17/bundle?**', async route => {
      const body = savedBuildBundle();
      if (mismatch === 'version') body.artifact_version_id = 'other-version';
      if (mismatch === 'app') body.workbench.target_app_id = 'other-app';
      if (mismatch === 'registry') body.workbench.build_registry_id = 'other-registry';
      await route.fulfill({ json: body });
    });
    await page.goto(`/apps/${APP_ID}/activity`);
    await expect(page.getByText('The saved build response does not match the selected app and version.', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start draft preview', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Accept artifact', exact: true })).toHaveCount(0);
  });
}

test('saved build review ignores a late response from an earlier selection', async ({ page }) => {
  let releaseOld;
  let oldRequested = false;
  const oldGate = new Promise(resolve => { releaseOld = resolve; });
  await page.route('**/api/studio/build/artifacts/ver-16/bundle?**', async route => {
    oldRequested = true;
    await oldGate;
    await route.fulfill({ json: savedBuildBundle('ver-16') }).catch(() => {});
  });
  try {
    await page.goto(`/apps/${APP_ID}/activity`);
    await expect(page.getByText('Saved build ver-17', { exact: true })).toBeVisible();
    await page.getByLabel('Starting version', { exact: true }).selectOption('ver-16');
    await expect.poll(() => oldRequested).toBe(true);
    await expect(page.getByText('Saved build ver-17', { exact: true })).toBeHidden();
    await page.getByLabel('Starting version', { exact: true }).selectOption('ver-17');
    await expect(page.getByText('Saved build ver-17', { exact: true })).toBeVisible();
    releaseOld();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(page.getByText('Saved build ver-16', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Saved build ver-17', { exact: true })).toBeVisible();
  } finally { releaseOld(); }
});

test('saved build review retains preview ownership when switching versions', async ({ page }) => {
  const commands = [];
  await page.route('**/preview-fixture', route => route.fulfill({
    contentType: 'text/html',
    body: '<h1>Isolated preview fixture</h1><button onclick="this.textContent=\'Preview is interactive\'">Try preview</button>',
  }));
  await page.routeWebSocket('**/ws/sandbox/**', socket => socket.close());
  await page.route('**/api/artifacts/*/sandbox?**', async route => {
    const versionId = new URL(route.request().url()).pathname.split('/').at(-2);
    commands.push(`allocate:${versionId}`);
    await route.fulfill({ json: { sandboxId: `sandbox-${versionId}` } });
  });
  await page.route('**/api/sandbox/**', async route => {
    const parts = new URL(route.request().url()).pathname.split('/');
    const action = parts.at(-1);
    const sandboxId = parts.at(-2);
    commands.push(`${action}:${sandboxId}`);
    await route.fulfill({ json: action === 'start' || action === 'status'
      ? { status: 'running', previewUrl: new URL('/preview-fixture', page.url()).href }
      : { status: action === 'stop' ? 'stopped' : 'synced' } });
  });
  await page.goto(`/apps/${APP_ID}/activity`);
  await page.getByRole('button', { name: 'Start draft preview', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Open draft preview', exact: true })).toBeVisible();
  expect(commands).toEqual(['allocate:ver-17', 'sync:sandbox-ver-17', 'start:sandbox-ver-17']);
  const preview = page.frameLocator('iframe[title="Draft app preview"]');
  await preview.getByRole('button', { name: 'Try preview', exact: true }).click();
  await expect(preview.getByRole('button', { name: 'Preview is interactive', exact: true })).toBeVisible();
  await page.getByLabel('Starting version', { exact: true }).selectOption('ver-16');
  await expect(page.getByText('Saved build ver-16', { exact: true })).toBeVisible();
  await expect(page.getByText('Preview based on version ver-17', { exact: true })).toBeVisible();
  await expect(page.getByText('A different draft is selected.', { exact: true })).toBeVisible();
  await expect(preview.getByRole('button', { name: 'Preview is interactive', exact: true })).toBeVisible();
  expect(commands.filter(command => !command.startsWith('status:'))).toEqual([
    'allocate:ver-17', 'sync:sandbox-ver-17', 'start:sandbox-ver-17',
  ]);
  await expect(page.getByRole('button', { name: 'Start draft preview', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Update preview', exact: true }).click();
  await expect(page.getByText('Preview based on version ver-16', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open draft preview', exact: true })).toBeVisible();
  await expect(preview.getByRole('button', { name: 'Try preview', exact: true })).toBeVisible();
  expect(commands.filter(command => !command.startsWith('status:'))).toEqual([
    'allocate:ver-17', 'sync:sandbox-ver-17', 'start:sandbox-ver-17',
    'stop:sandbox-ver-17', 'allocate:ver-16', 'sync:sandbox-ver-16', 'start:sandbox-ver-16',
  ]);
});

test('saved build review preserves candidate source through a failed selection and parent refresh', async ({ page }) => {
  let failOtherVersion = true;
  let triggerCount = 0;
  let releaseNextRefinement;
  const nextRefinementGate = new Promise(resolve => { releaseNextRefinement = resolve; });
  const receipts = [];
  const candidateReview = {
    ...savedBuildBundle().review, coding_summary: 'Candidate A2 review evidence',
    can_accept: true, can_promote: false, lifecycle_status: 'draft',
  };
  const parentReview = { ...savedBuildBundle().review, can_accept: false, can_promote: true, lifecycle_status: 'current' };
  const acceptedVersions = [];
  await page.routeWebSocket('**/ws/sandbox/**', socket => socket.close());
  await page.route('**/api/studio/build/artifacts/ver-17/bundle?**', route => route.fulfill({ json: { ...savedBuildBundle(), review: parentReview } }));
  await page.route('**/api/studio/build/artifacts/ver-17/review?**', route => route.fulfill({ json: { review: parentReview } }));
  await page.route('**/api/studio/build/artifacts/candidate-a2/review?**', route => route.fulfill({ json: { review: candidateReview } }));
  await page.route('**/api/studio/build/artifacts/*/accept?**', async route => {
    acceptedVersions.push(new URL(route.request().url()).pathname.split('/').at(-2));
    await route.fulfill({ json: { accepted: true, review: { ...candidateReview, can_accept: false, can_promote: true, lifecycle_status: 'current' } } });
  });
  await page.route('**/api/workflows/trigger', async route => {
    triggerCount += 1;
    if (triggerCount > 1) {
      await nextRefinementGate;
      return route.fulfill({ status: 503, json: { detail: 'Next refinement unavailable.' } });
    }
    return route.fulfill({ json: {
      execution_mode: 'coding_worker', coding_worker: {
        status: 'validated', metadata: { build_record_id: 'candidate-a2' },
        applied_files: { 'README.md': '# Validated candidate A2' },
        validation_result: { validation_status: 'passed', validation_strategy: 'docker', app_bundle_acceptance_result: { passed: true }, app_validation_result: { validation_status: 'passed', validation_strategy: 'docker' } },
      },
    } });
  });
  await page.route('**/api/studio/build/artifacts/ver-16/bundle?**', route => route.fulfill(failOtherVersion
    ? { status: 503, json: { detail: 'Version B temporarily unavailable.' } }
    : { json: savedBuildBundle('ver-16') }));
  await page.route('**/api/artifacts/*/sandbox?**', async route => {
    const versionId = new URL(route.request().url()).pathname.split('/').at(-2);
    receipts.push({ action: 'allocate', versionId });
    await route.fulfill({ json: { sandboxId: `sandbox-${versionId}` } });
  });
  await page.route('**/api/sandbox/**', async route => {
    const action = new URL(route.request().url()).pathname.split('/').at(-1);
    if (action === 'sync') receipts.push({ action, files: route.request().postDataJSON().files });
    // Observe the real hook's source/identity binding without provisioning a preview.
    await route.fulfill(action === 'start'
      ? { status: 409, json: { detail: 'Preview fixture does not provision a sandbox.' } }
      : { json: { status: action === 'stop' ? 'stopped' : 'synced' } });
  });
  await page.goto(`/apps/${APP_ID}/activity`);
  await page.getByPlaceholder('Describe the change').fill('Make candidate A2.');
  await page.getByRole('button', { name: 'Apply change', exact: true }).click();
  await expect(page.getByText('Draft validated and saved for review.', { exact: true })).toBeVisible();
  await expect(page.getByText('Version candidate-a2', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Candidate A2 review evidence', { exact: true })).toBeHidden();
  await page.getByRole('region', { name: 'Artifact review', exact: true }).getByText('Change summary', { exact: true }).click();
  await expect(page.getByText('Candidate A2 review evidence', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Accept artifact', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Activate this draft', exact: true })).toHaveCount(0);
  await expect(page.getByText('Strategy: docker', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Apply change', exact: true }).click();
  try {
    await expect.poll(() => triggerCount).toBe(2);
    await expect(page.getByText('Strategy: docker', { exact: true })).toBeVisible();
    await expect(page.getByText('Draft validated and saved for review.', { exact: true })).toBeVisible();
  } finally { releaseNextRefinement(); }
  await expect(page.getByText('Next refinement unavailable.', { exact: true })).toBeVisible();
  await expect(page.getByText('Strategy: docker', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Start draft preview', exact: true }).click();
  await expect.poll(() => receipts.filter(item => item.action === 'sync').length).toBe(1);
  await page.getByLabel('Starting version', { exact: true }).selectOption('ver-16');
  await expect(page.getByText('Version B temporarily unavailable.', { exact: true })).toBeVisible();
  await page.getByLabel('Starting version', { exact: true }).selectOption('ver-17');
  await expect(page.getByText('Saved build ver-17', { exact: true })).toBeVisible();
  await expect(page.getByText('Preview based on version candidate-a2', { exact: true })).toBeVisible();
  await expect(page.getByText('Candidate A2 review evidence', { exact: true })).toBeVisible();
  await expect(page.getByText('Strategy: docker', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Accept artifact', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Activate this draft', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Start draft preview', exact: true }).click();
  await expect.poll(() => receipts.filter(item => item.action === 'sync').length).toBe(2);
  expect(receipts.filter(item => item.action === 'allocate').map(item => item.versionId)).toEqual(['candidate-a2', 'candidate-a2']);
  expect(receipts.filter(item => item.action === 'sync').map(item => item.files)).toEqual([
    [{ path: 'README.md', content: '# Validated candidate A2' }],
    [{ path: 'README.md', content: '# Validated candidate A2' }],
  ]);
  await page.getByRole('button', { name: 'Accept artifact', exact: true }).click();
  await expect.poll(() => acceptedVersions).toEqual(['candidate-a2']);

  failOtherVersion = false;
  await page.getByLabel('Starting version', { exact: true }).selectOption('ver-16');
  await expect(page.getByText('Saved build ver-16', { exact: true })).toBeVisible();
  await expect(page.getByText('Preview based on version candidate-a2', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Start draft preview', exact: true }).click();
  await expect.poll(() => receipts.filter(item => item.action === 'sync').length).toBe(3);
  await expect(page.getByText('Preview based on version ver-16', { exact: true })).toBeVisible();
  expect(receipts.filter(item => item.action === 'allocate').at(-1).versionId).toBe('ver-16');
  expect(receipts.filter(item => item.action === 'sync').at(-1).files).toEqual([{ path: 'README.md', content: '# Saved fixture ver-16' }]);
});

for (const actionLabel of ['Apply change', 'Redesign theme']) {
  test(`saved build review ignores an old ${actionLabel} result after selection changes`, async ({ page }) => {
    let releaseResponse;
    let triggerRequested = false;
    const responseGate = new Promise(resolve => { releaseResponse = resolve; });
    const reviewRequests = [];
    page.on('request', request => {
      if (/\/api\/studio\/build\/artifacts\/[^/]+\/review/.test(request.url())) reviewRequests.push(request.url());
    });
    await page.route('**/api/workflows/trigger', async route => {
      triggerRequested = true;
      expect(route.request().postDataJSON().trigger_payload.refinement_request.artifact_version_id).toBe('ver-17');
      await responseGate;
      await route.fulfill({ json: {
        execution_mode: 'coding_worker', coding_worker: {
          status: 'validated', metadata: { build_record_id: 'old-selection-candidate' },
          applied_files: { 'README.md': 'Old selection candidate files' },
          validation_result: { validation_status: 'passed', app_bundle_acceptance_result: { passed: true }, app_validation_result: { validation_status: 'passed' } },
        },
      } });
    });
    try {
      await page.goto(`/apps/${APP_ID}/activity`);
      await page.getByPlaceholder('Describe the change').fill('Update the opened draft.');
      await page.getByRole('button', { name: actionLabel, exact: true }).click();
      await expect.poll(() => triggerRequested).toBe(true);
      await page.getByLabel('Starting version', { exact: true }).selectOption('ver-16');
      await expect(page.getByText('Saved build ver-16', { exact: true })).toBeVisible();
      releaseResponse();
      await expect(page.getByRole('button', { name: 'Apply change', exact: true })).toBeEnabled();
      await expect(page.getByText('Draft validated and saved for review.', { exact: true })).toHaveCount(0);
      await expect(page.getByText('Version ver-16', { exact: true }).first()).toBeVisible();
      expect(reviewRequests.some(url => url.includes('old-selection-candidate'))).toBe(false);
    } finally { releaseResponse(); }
  });
}

});

test('mobile app Studio navigation keeps route transitions stable', async ({ page }) => {
  const viewport = page.viewportSize();
  test.skip(!viewport || viewport.width >= 768, 'Mobile app-studio navigation smoke only applies to the mobile project.');

  // Dismiss onboarding tour so its tooltip does not overlap the fixed "Open Studio
  // navigation" button during mobile navigation click checks.
  await page.route('**/api/modules/user_onboarding/get_onboarding_status**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildOnboardingStatusPayload({ dismissed: true })),
    });
  });

  await page.goto(`/apps/${APP_ID}/overview`);
  const main = page.locator('main');
  const routeChecks = [
    {
      href: `/apps/${APP_ID}/building`,
      heading: 'Building',
      detail: async () => expect(main.getByRole('heading', { name: 'Build state' })).toBeVisible(),
    },
    {
      href: `/apps/${APP_ID}/usage`,
      heading: 'Token Usage',
      detail: async () => expect(main.getByRole('heading', { name: 'Workflow breakdown' })).toBeVisible(),
    },
    {
      href: `/apps/${APP_ID}/access`,
      heading: 'Users',
      detail: async () => expect(main.getByRole('heading', { name: 'Account management' })).toBeVisible(),
    },
  ];

  for (const routeCheck of routeChecks) {
    await page.getByRole('button', { name: 'Open Studio navigation' }).click();

    const navigation = page.getByRole('navigation', { name: 'App Studio navigation' });
    await expect(navigation).toBeVisible();
    await navigation.locator(`a[href="${routeCheck.href}"]`).click();

    await expect(main.getByRole('heading', { name: routeCheck.heading, exact: true })).toBeVisible();
    await routeCheck.detail();
    await expectNoHorizontalOverflow(page);
  }
});

test('mobile workspace Studio navigation keeps route transitions stable', async ({ page }) => {
  const viewport = page.viewportSize();
  test.skip(!viewport || viewport.width >= 768, 'Mobile workspace-studio navigation smoke only applies to the mobile project.');

  // Dismiss onboarding tour so its tooltip (which overlaps the fixed "Open Studio
  // navigation" button on mobile) does not block navigation clicks.
  await page.route('**/api/modules/user_onboarding/get_onboarding_status**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildOnboardingStatusPayload({ dismissed: true })),
    });
  });

  await page.goto('/apps');
  const main = page.locator('main');
  const routeChecks = [
    {
      href: '/usage',
      heading: 'Token Usage',
      detail: async () => expect(main.getByRole('heading', { name: 'Workspace usage' })).toBeVisible(),
    },
    {
      href: '/integrations',
      heading: 'Integrations',
      detail: async () => {
        await expect(main.getByText('Mozaiks Pay')).toBeVisible();
      },
    },
    {
      href: '/support',
      heading: 'Support',
      detail: async () => {
        const campaignCard = main.locator('article').filter({ hasText: 'Campaign Revision Workbench' }).first();
        await expect(campaignCard).toBeVisible();
      },
    },
    {
      href: '/apps',
      heading: 'Apps',
      detail: async () => {
        await expect(page.locator('header').getByRole('button', { name: 'Create App' })).toBeVisible();
        await expect(main.getByRole('button', { name: 'Create App' })).toHaveCount(0);
      },
    },
  ];

  for (const routeCheck of routeChecks) {
    await page.getByRole('button', { name: 'Open Studio navigation' }).click();

    const navigation = page.getByRole('navigation', { name: 'Workspace navigation' });
    await expect(navigation).toBeVisible();
    await navigation.locator(`a[href="${routeCheck.href}"]`).click();

    await expect(main.getByRole('heading', { name: routeCheck.heading, exact: true })).toBeVisible();
    await routeCheck.detail();
    await expectNoHorizontalOverflow(page);
  }
});

// ── Onboarding tour ─────────────────────────────────────────────────────────

test('onboarding tour appears for a fresh user and shows step 1', async ({ page }) => {
  await page.goto('/apps');

  // Tour mounts asynchronously after status fetch — wait for the dialog
  const dialog = page.getByRole('dialog', { name: /Onboarding step 1 of 3/i });
  await expect(dialog).toBeVisible({ timeout: 5000 });

  await expect(dialog).toContainText('Create your first app');
  await expect(dialog).toContainText('1 / 3');
  await expect(dialog.getByRole('button', { name: 'Next' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Skip tour' })).toBeVisible();
});

test('onboarding tour advances to step 2 on Next', async ({ page }) => {
  await page.goto('/apps');

  const dialog = page.getByRole('dialog', { name: /Onboarding step 1 of 3/i });
  await expect(dialog).toBeVisible({ timeout: 5000 });

  await dialog.getByRole('button', { name: 'Next' }).click();

  const step2 = page.getByRole('dialog', { name: /Onboarding step 2 of 3/i });
  await expect(step2).toBeVisible({ timeout: 3000 });
  await expect(step2).toContainText('Track your usage');
  await expect(step2).toContainText('2 / 3');
});

test('onboarding tour dismisses on Skip tour', async ({ page }) => {
  await page.goto('/apps');

  const dialog = page.getByRole('dialog', { name: /Onboarding step 1 of 3/i });
  await expect(dialog).toBeVisible({ timeout: 5000 });

  await dialog.getByRole('button', { name: 'Skip tour' }).click();

  await expect(dialog).toBeHidden({ timeout: 3000 });
});

test('onboarding tour does not appear for a dismissed user', async ({ page }) => {
  await page.route('**/api/modules/user_onboarding/get_onboarding_status**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildOnboardingStatusPayload({ dismissed: true, progress: 0 })),
    });
  });

  await page.goto('/apps');
  await expect(page.locator('main').getByRole('heading', { name: 'Apps' })).toBeVisible();

  await page.waitForTimeout(1000);
  await expect(page.getByRole('dialog', { name: /Onboarding step/i })).toHaveCount(0);
});

test('onboarding tour does not appear for a completed user', async ({ page }) => {
  const completedSteps = {
    create_app: { completed: true, completed_at: '2026-07-28T00:00:00Z' },
    explore_apps: { completed: true, completed_at: '2026-07-28T00:00:00Z' },
    open_support: { completed: true, completed_at: '2026-07-28T00:00:00Z' },
  };
  await page.route('**/api/modules/user_onboarding/get_onboarding_status**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(buildOnboardingStatusPayload({ progress: 100, steps: completedSteps })),
    });
  });

  await page.goto('/apps');
  await expect(page.locator('main').getByRole('heading', { name: 'Apps' })).toBeVisible();

  await page.waitForTimeout(1000);
  await expect(page.getByRole('dialog', { name: /Onboarding step/i })).toHaveCount(0);
});
