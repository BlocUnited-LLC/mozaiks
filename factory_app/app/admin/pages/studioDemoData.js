const DEMO_APPS = [
  {
    build_registry_id: 'demo_appreg_partner-deploy',
    app_id: 'partner-delivery-studio',
    name: 'Partner Delivery Studio',
    description: 'Authenticated partner operations with managed deployment and release checks.',
    status: 'active',
    archetype_id: 'admin_operations_dashboard',
    canonical_structure: 'mozaiks.app.v1',
    functional_acceptance: 'passed',
    portal_focus: ['building', 'launch', 'integrations', 'governance'],
    created_at: '2025-01-19T08:00:00Z',
    updated_at: '2025-02-05T11:20:00Z',
  },
  {
    build_registry_id: 'demo_appreg_member-growth',
    app_id: 'member-growth-studio',
    name: 'Member Growth Studio',
    description: 'Monetized AI workflows with usage, billing, access, and operator alerts.',
    status: 'active',
    archetype_id: 'monetized_saas_reports',
    canonical_structure: 'mozaiks.app.v1',
    functional_acceptance: 'passed',
    portal_focus: ['billing', 'usage', 'access', 'activity'],
    created_at: '2025-01-10T13:10:00Z',
    updated_at: '2025-02-05T16:40:00Z',
  },
  {
    build_registry_id: 'demo_appreg_campaign-revision',
    app_id: 'campaign-revision-workbench',
    name: 'Campaign Revision Workbench',
    description: 'Authenticated campaign CRUD, review queues, and approval workflows.',
    status: 'active',
    archetype_id: 'authenticated_crud_projects',
    canonical_structure: 'mozaiks.app.v1',
    functional_acceptance: 'passed',
    portal_focus: ['overview', 'branding', 'access', 'settings', 'support'],
    created_at: '2025-01-15T09:30:00Z',
    updated_at: '2025-02-04T18:25:00Z',
  },
]

const DEMO_RUNTIME_SUMMARY = {
  integrations: {
    llm: {
      label: 'LLM Provider',
      kind: 'llm',
      configured: true,
      primary_model: 'gpt-5-nano',
    },
    database: {
      label: 'MongoDB',
      kind: 'database',
      configured: true,
    },
    sandbox: {
      label: 'Sandbox',
      kind: 'sandbox',
      configured: true,
    },
    auth: {
      label: 'Authentication',
      kind: 'auth',
      configured: true,
      enabled: true,
      provider: 'mock',
    },
    backend: {
      label: 'App Backend',
      kind: 'backend',
      configured: true,
      url: 'http://localhost:8000',
    },
    connector_vault: {
      label: 'Connector Vault',
      kind: 'vault',
      configured: true,
      provider: 'local',
      mode: 'demo',
    },
  },
}

const DEMO_APP_CONNECTORS = {
  'partner-delivery-studio': [
    { service: 'github', display_name: 'GitHub', secret_available: true, status: 'active' },
  ],
  'member-growth-studio': [
    { service: 'slack', display_name: 'Slack', secret_available: true, status: 'active' },
    { service: 'segment', display_name: 'Segment', secret_available: true, status: 'active' },
  ],
  'campaign-revision-workbench': [
    { service: 'slack', display_name: 'Slack', secret_available: true, status: 'active' },
  ],
}

const DEMO_USAGE_BY_APP = {
  'partner-delivery-studio': {
    tokens_used: 348000,
    llm_cost_usd: 912.4,
    workflow_runs: 4210,
    tool_calls: 13220,
    errors: 4,
  },
  'member-growth-studio': {
    tokens_used: 1842000,
    llm_cost_usd: 4825.33,
    workflow_runs: 18240,
    tool_calls: 66100,
    errors: 18,
  },
  'campaign-revision-workbench': {
    tokens_used: 21000,
    llm_cost_usd: 54.08,
    workflow_runs: 190,
    tool_calls: 620,
    errors: 2,
  },
}

const DEMO_BILLING_BY_APP = {
  'partner-delivery-studio': {
    total_revenue_usd: 52400,
    mrr_usd: 9200,
    arr_usd: 110400,
    active_customers: 54,
    failed_payments: 1,
  },
  'member-growth-studio': {
    total_revenue_usd: 184200,
    mrr_usd: 27800,
    arr_usd: 333600,
    active_customers: 482,
    failed_payments: 4,
  },
  'campaign-revision-workbench': {
    total_revenue_usd: 9700,
    mrr_usd: 1600,
    arr_usd: 19200,
    active_customers: 12,
    failed_payments: 1,
  },
}

const DEMO_DEPLOYMENT_BY_APP = {
  'partner-delivery-studio': {
    deployment_label: 'Live',
    failed: false,
    domain_count: 1,
    domains: ['partner-studio.mozaiks.app'],
    environment: 'Production',
    bandwidth_gb: 180,
    storage_gb: 22,
    uptime_percent: 99.93,
  },
  'member-growth-studio': {
    deployment_label: 'Live',
    failed: false,
    domain_count: 2,
    domains: ['growth.mozaiks.app', 'api-growth.mozaiks.app'],
    environment: 'Production',
    bandwidth_gb: 920,
    storage_gb: 58,
    uptime_percent: 99.96,
  },
  'campaign-revision-workbench': {
    deployment_label: 'Live',
    failed: false,
    domain_count: 1,
    domains: ['campaign-review.mozaiks.app'],
    environment: 'Production',
    bandwidth_gb: 42,
    storage_gb: 5,
    uptime_percent: 99.89,
  },
}

const DEMO_USERS_BY_APP = {
  'partner-delivery-studio': {
    total_users: 204,
    active_users: 163,
    new_users: 21,
    churned_users: 8,
    users: [
      { id: 'usr_partner_1', name: 'Mina Perez', email: 'mina@example.com', segment: 'Partner ops', status: 'active', subscription: 'Enterprise', last_seen: '27 minutes ago' },
      { id: 'usr_partner_2', name: 'Devin Moss', email: 'devin@example.com', segment: 'Partner managers', status: 'active', subscription: 'Enterprise', last_seen: '3 hours ago' },
    ],
    segments: [
      { label: 'Partner ops', count: 102 },
      { label: 'Partner managers', count: 48 },
      { label: 'Finance reviewers', count: 27 },
    ],
    support_history: [
      { id: 'sup_partner_1', label: 'Provisioning ticket', detail: 'Preview environment access issue resolved today.' },
    ],
  },
  'member-growth-studio': {
    total_users: 2480,
    active_users: 1824,
    new_users: 206,
    churned_users: 74,
    users: [
      { id: 'usr_growth_1', name: 'Nora Patel', email: 'nora@example.com', segment: 'Growth operators', status: 'active', subscription: 'Enterprise', last_seen: '8 minutes ago' },
      { id: 'usr_growth_2', name: 'Sean Kim', email: 'sean@example.com', segment: 'Success managers', status: 'active', subscription: 'Pro', last_seen: '41 minutes ago' },
      { id: 'usr_growth_3', name: 'Ariel Long', email: 'ariel@example.com', segment: 'Campaign reviewers', status: 'active', subscription: 'Enterprise', last_seen: 'Today' },
    ],
    segments: [
      { label: 'Growth operators', count: 842 },
      { label: 'Success managers', count: 510 },
      { label: 'Campaign reviewers', count: 294 },
    ],
    support_history: [
      { id: 'sup_growth_1', label: 'Renewal support', detail: '4 customer billing questions closed in the last 24 hours.' },
    ],
  },
  'campaign-revision-workbench': {
    total_users: 19,
    active_users: 11,
    new_users: 2,
    churned_users: 1,
    users: [
      { id: 'usr_campaign_1', name: 'Owen Gray', email: 'owen@example.com', segment: 'Revision reviewers', status: 'active', subscription: 'Starter', last_seen: 'Yesterday' },
    ],
    segments: [
      { label: 'Revision reviewers', count: 8 },
      { label: 'Campaign owners', count: 11 },
    ],
    support_history: [
      { id: 'sup_campaign_1', label: 'Revision blocker', detail: '1 open revision blocker is affecting user rollout.' },
    ],
  },
}

const DEMO_ACTIVITY_BY_APP = {
  'partner-delivery-studio': [
    { id: 'act_partner_1', title: 'Acceptance passed', detail: 'Canonical admin operations checks passed for the current bundle.', timestamp: '18 minutes ago' },
    { id: 'act_partner_2', title: 'Production domain verified', detail: 'The managed production domain is healthy.', timestamp: 'Today' },
  ],
  'member-growth-studio': [
    { id: 'act_growth_1', title: 'Usage spike detected', detail: 'Campaign scoring workflows crossed the daily run threshold.', timestamp: '12 minutes ago' },
    { id: 'act_growth_2', title: 'Customer billing synced', detail: 'Billing data refreshed from the managed billing integration.', timestamp: '1 hour ago' },
  ],
  'campaign-revision-workbench': [
    { id: 'act_campaign_1', title: 'Acceptance passed', detail: 'CRUD, authentication, and approval-route checks passed.', timestamp: '2 hours ago' },
  ],
}

const DEMO_WORKFLOWS_BY_APP = {
  'partner-delivery-studio': ['DeployWorkflow', 'PartnerSyncWorkflow', 'PayoutOpsWorkflow'],
  'member-growth-studio': ['GrowthScoringWorkflow', 'CampaignReviewWorkflow', 'RetentionSignalsWorkflow'],
  'campaign-revision-workbench': ['RevisionWorkflow'],
}

const DEMO_RUNS_BY_APP = {
  'partner-delivery-studio': [
    { chat_id: 'run_partner_1', workflow_name: 'DeployWorkflow', user_id: 'mina@example.com', agent_turns: 22, tool_calls: 73, errors: 0, prompt_tokens: 13200, completion_tokens: 4100, cost: 28.9, runtime_sec: 133.4, ended_at: null, started_at: '2025-02-05T18:02:00Z', status: 0 },
    { chat_id: 'run_partner_2', workflow_name: 'PartnerSyncWorkflow', user_id: 'devin@example.com', agent_turns: 31, tool_calls: 117, errors: 0, prompt_tokens: 18800, completion_tokens: 5200, cost: 42.8, runtime_sec: 188.1, ended_at: '2025-02-05T14:32:00Z', started_at: '2025-02-05T14:08:00Z', status: 1 },
  ],
  'member-growth-studio': [
    { chat_id: 'run_growth_1', workflow_name: 'GrowthScoringWorkflow', user_id: 'nora@example.com', agent_turns: 94, tool_calls: 320, errors: 2, prompt_tokens: 64000, completion_tokens: 18000, cost: 182.5, runtime_sec: 421.6, ended_at: null, started_at: '2025-02-05T18:10:00Z', status: 0 },
    { chat_id: 'run_growth_2', workflow_name: 'CampaignReviewWorkflow', user_id: 'sean@example.com', agent_turns: 63, tool_calls: 245, errors: 1, prompt_tokens: 48100, completion_tokens: 12100, cost: 135.9, runtime_sec: 305.4, ended_at: '2025-02-05T15:11:00Z', started_at: '2025-02-05T14:42:00Z', status: 1 },
    { chat_id: 'run_growth_3', workflow_name: 'RetentionSignalsWorkflow', user_id: 'ariel@example.com', agent_turns: 28, tool_calls: 102, errors: 0, prompt_tokens: 15400, completion_tokens: 4200, cost: 38.2, runtime_sec: 141.5, ended_at: '2025-02-05T12:15:00Z', started_at: '2025-02-05T12:01:00Z', status: 1 },
  ],
  'campaign-revision-workbench': [
    { chat_id: 'run_campaign_1', workflow_name: 'RevisionWorkflow', user_id: 'owen@example.com', agent_turns: 8, tool_calls: 22, errors: 0, prompt_tokens: 3200, completion_tokens: 1200, cost: 8.4, runtime_sec: 51.2, ended_at: '2025-02-05T10:21:00Z', started_at: '2025-02-05T10:12:00Z', status: 1 },
  ],
}

const DEMO_USAGE_BASE_DATE = '2026-07-11T16:00:00Z'

const DEMO_USAGE_DAY_SHAPE = [
  0.22, 0.34, 0.28, 0.44, 0.52, 0.18, 0.16,
  0.48, 0.58, 0.62, 0.74, 0.68, 0.24, 0.22,
  0.72, 0.82, 0.94, 1.12, 1.0, 0.36, 0.32,
  0.88, 1.04, 1.18, 1.42, 1.24, 0.54, 0.5,
]

const DEMO_USAGE_PROFILES = {
  'member-growth-studio': {
    baseChats: 8,
    promptBase: 9200,
    completionBase: 1800,
    callsBase: 4,
    workflows: ['GrowthScoringWorkflow', 'CampaignReviewWorkflow', 'RetentionSignalsWorkflow'],
    users: ['nora@example.com', 'sean@example.com', 'ariel@example.com', 'growth-ops@example.com'],
  },
  'partner-delivery-studio': {
    baseChats: 5,
    promptBase: 7600,
    completionBase: 1450,
    callsBase: 3,
    workflows: ['DeployWorkflow', 'PartnerSyncWorkflow', 'PayoutOpsWorkflow'],
    users: ['mina@example.com', 'devin@example.com', 'partners@example.com'],
  },
  'campaign-revision-workbench': {
    baseChats: 2,
    promptBase: 4200,
    completionBase: 820,
    callsBase: 2,
    workflows: ['RevisionWorkflow'],
    users: ['owen@example.com', 'review@example.com'],
  },
}

function addUtcDays(date, days) {
  const next = new Date(date)
  next.setUTCDate(next.getUTCDate() + days)
  return next
}

function roundMoney(value) {
  return Math.round(Number(value || 0) * 1_000_000) / 1_000_000
}

function getDemoApp(appId) {
  return DEMO_APPS.find((entry) => entry.app_id === appId) || null
}

function buildDemoUsageRows(appIdFilter = null) {
  const baseDate = new Date(DEMO_USAGE_BASE_DATE)
  const firstDayOffset = -1 * (DEMO_USAGE_DAY_SHAPE.length - 1)
  const rows = []

  Object.entries(DEMO_USAGE_PROFILES).forEach(([appId, profile], appIndex) => {
    if (appIdFilter && appId !== appIdFilter) return

    const app = getDemoApp(appId)
    DEMO_USAGE_DAY_SHAPE.forEach((shape, dayIndex) => {
      const day = addUtcDays(baseDate, firstDayOffset + dayIndex)
      const weekday = day.getUTCDay()
      const weekendPenalty = weekday === 0 || weekday === 6 ? -1 : 0
      const chats = Math.max(
        0,
        Math.round(profile.baseChats * shape + ((dayIndex + appIndex) % 4 === 0 ? 1 : 0) + weekendPenalty),
      )

      for (let chatIndex = 0; chatIndex < chats; chatIndex += 1) {
        const seed = (dayIndex + 3) * (chatIndex + 5) * (appIndex + 2)
        const promptMultiplier = 0.82 + ((seed % 7) * 0.055)
        const completionMultiplier = 0.78 + ((seed % 5) * 0.075)
        const promptTokens = Math.round(profile.promptBase * promptMultiplier)
        const completionTokens = Math.round(profile.completionBase * completionMultiplier)
        const llmCalls = Math.max(1, Math.round(profile.callsBase + (seed % 3)))
        const eventTime = new Date(day)
        eventTime.setUTCHours(9 + ((chatIndex + appIndex) % 9), (dayIndex * 7 + chatIndex * 11) % 60, 0, 0)
        const runtimeSec = Math.round(45 + (promptTokens + completionTokens) / 180 + (seed % 45))
        const estimatedCost = roundMoney((promptTokens * 0.00000015) + (completionTokens * 0.0000006))
        const workflow = profile.workflows[(dayIndex + chatIndex) % profile.workflows.length]

        rows.push({
          chat_id: `demo-chat-${appId}-${dayIndex + 1}-${chatIndex + 1}`,
          app_id: appId,
          app_name: app?.name || appId,
          workflow_name: workflow,
          user_id: profile.users[(dayIndex + chatIndex) % profile.users.length],
          event_ts: eventTime.toISOString(),
          started_at: eventTime.toISOString(),
          ended_at: new Date(eventTime.getTime() + runtimeSec * 1000).toISOString(),
          prompt_tokens: promptTokens,
          completion_tokens: completionTokens,
          total_tokens: promptTokens + completionTokens,
          cached_prompt_tokens: seed % 6 === 0 ? Math.round(promptTokens * 0.18) : 0,
          estimated_cost_usd: estimatedCost,
          cost: estimatedCost,
          cost_source: 'catalog',
          model_name: seed % 9 === 0 ? 'gpt-4o-mini-2024-07-18' : 'gpt-5-nano',
          llm_calls: llmCalls,
          agent_turns: Math.max(2, llmCalls * 2 + (seed % 4)),
          tool_calls: Math.max(1, llmCalls + (seed % 6)),
          errors: seed % 37 === 0 ? 1 : 0,
          runtime_sec: runtimeSec,
          status: 1,
        })
      }
    })
  })

  return rows.sort((left, right) => new Date(right.event_ts).getTime() - new Date(left.event_ts).getTime())
}

function summarizeDemoUsageRows(rows, appId = null) {
  const sourceRows = Array.isArray(rows) ? rows : []
  const totals = sourceRows.reduce((acc, row) => {
    acc.prompt_tokens += Number(row.prompt_tokens || 0)
    acc.completion_tokens += Number(row.completion_tokens || 0)
    acc.total_tokens += Number(row.total_tokens || 0)
    acc.cached_prompt_tokens += Number(row.cached_prompt_tokens || 0)
    acc.estimated_cost_usd = roundMoney(acc.estimated_cost_usd + Number(row.estimated_cost_usd || 0))
    acc.llm_calls += Number(row.llm_calls || 0)
    return acc
  }, {
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    cached_prompt_tokens: 0,
    estimated_cost_usd: 0,
    llm_calls: 0,
  })

  const workflowGroups = new Map()
  for (const row of sourceRows) {
    const workflow = row.workflow_name || 'Unknown workflow'
    if (!workflowGroups.has(workflow)) {
      workflowGroups.set(workflow, {
        workflow_name: workflow,
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
        cached_prompt_tokens: 0,
        estimated_cost_usd: 0,
        llm_calls: 0,
        runs: 0,
      })
    }
    const current = workflowGroups.get(workflow)
    current.prompt_tokens += Number(row.prompt_tokens || 0)
    current.completion_tokens += Number(row.completion_tokens || 0)
    current.total_tokens += Number(row.total_tokens || 0)
    current.cached_prompt_tokens += Number(row.cached_prompt_tokens || 0)
    current.estimated_cost_usd = roundMoney(current.estimated_cost_usd + Number(row.estimated_cost_usd || 0))
    current.llm_calls += Number(row.llm_calls || 0)
    current.runs += 1
  }

  const usedModels = Array.from(new Set(sourceRows.map((row) => row.model_name).filter(Boolean))).sort()

  return {
    app_id: appId,
    user_id: null,
    totals,
    by_workflow: Array.from(workflowGroups.values()).sort((left, right) => right.total_tokens - left.total_tokens),
    by_run: sourceRows,
    events: sourceRows.map((row) => ({
      ...row,
      event_id: row.chat_id,
      source: 'demo_usage_event',
    })),
    source: 'demo_usage_events',
    cost_source: 'catalog',
    pricing_health: {
      status: 'ready',
      catalogs: [
        {
          kind: 'catalog',
          env_name: null,
          path: 'ai-pricing/catalogs/usage-pricing.generated.json',
          exists: true,
          status: 'ready',
          error: null,
          model_count: 2353,
          source_name: 'litellm',
          source_url: 'https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json',
          source_revision: 'demo',
          fetched_at: DEMO_USAGE_BASE_DATE,
        },
      ],
      catalog_model_count: 2353,
      catalog_updated_at: DEMO_USAGE_BASE_DATE,
      used_model_count: usedModels.length,
      used_models: usedModels,
      priced_event_count: sourceRows.length,
      unpriced_event_count: 0,
      unpriced_model_count: 0,
      unpriced_models: [],
      default_table_event_count: 0,
      default_table_models: [],
      cost_source_counts: { catalog: sourceRows.length },
      coverage_percent: 100,
    },
    token_budget_alerts: [],
  }
}

const DEMO_SESSIONS_BY_APP = Object.fromEntries(
  Object.entries(DEMO_RUNS_BY_APP).map(([appId, runs]) => [
    appId,
    runs.map((run) => ({
      id: run.chat_id,
      app_id: appId,
      workflow_name: run.workflow_name,
      status: run.status,
      created_at: run.started_at,
      ended_at: run.ended_at,
      duration_sec: run.runtime_sec,
      telemetry_source: 'ag2_opentelemetry',
    })),
  ]),
)

const DEMO_BUILD_HISTORY_BY_APP = {
  'partner-delivery-studio': [
    { id: 'artifact_partner_2', version_number: 6, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-05T17:32:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/partner-delivery-studio/6/app.zip' } } },
    { id: 'artifact_partner_1', version_number: 5, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-03T12:40:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/partner-delivery-studio/5/app.zip' } } },
  ],
  'member-growth-studio': [
    { id: 'artifact_growth_3', version_number: 11, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-05T18:05:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/member-growth-studio/11/app.zip' } } },
    { id: 'artifact_growth_2', version_number: 10, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-02T13:22:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/member-growth-studio/10/app.zip' } } },
    { id: 'artifact_growth_1', version_number: 9, lifecycle_status: 'draft', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-01-29T08:30:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/member-growth-studio/9/app.zip' } } },
  ],
  'campaign-revision-workbench': [
    { id: 'artifact_campaign_1', version_number: 5, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-05T11:18:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/campaign-revision-workbench/5/app.zip' } } },
  ],
}

export function isStudioDemoModeEnabled() {
  return Boolean(
    typeof import.meta !== 'undefined'
      && import.meta.env?.DEV
      && String(import.meta.env?.VITE_MOCK_MODE || '').toLowerCase() === 'true',
  )
}

export function isStudioUsageDemoModeEnabled() {
  return Boolean(
    typeof import.meta !== 'undefined'
      && import.meta.env?.DEV
      && String(import.meta.env?.VITE_USAGE_DEMO_MODE || '').toLowerCase() === 'true',
  )
}

export function isStudioDemoApp(appId) {
  return DEMO_APPS.some((entry) => entry.app_id === appId)
}

export function buildStudioDemoApps() {
  return DEMO_APPS.map((app) => ({ ...app }))
}

export function buildStudioDemoAppSummary(appId) {
  const app = DEMO_APPS.find((entry) => entry.app_id === appId) || DEMO_APPS[0]
  const workflows = DEMO_WORKFLOWS_BY_APP[app?.app_id] || []
  return {
    app: {
      id: app?.app_id,
      app_id: app?.app_id,
      name: app?.name,
      description: app?.description,
      build_registry_id: app?.build_registry_id,
      archetype_id: app?.archetype_id,
      canonical_structure: app?.canonical_structure,
      functional_acceptance: app?.functional_acceptance,
      portal_focus: [...(app?.portal_focus || [])],
      lifecycle_state: app?.status || 'draft',
      lifecycle_label: String(app?.status || 'draft')
        .replaceAll('_', ' ')
        .replace(/\b\w/g, (match) => match.toUpperCase()),
      updated_at: app?.updated_at,
      created_at: app?.created_at,
    },
    ai: {
      provider: 'openai',
      model: 'gpt-5-nano',
      control_plane: {
        enabled: true,
        profile: 'default',
        classifier_enabled: true,
        classifier_llm_profile: 'classifier',
        coding_enabled: true,
        coding_llm_profile: 'codegen',
        llm_profile_count: 6,
      },
    },
    theme: {
      primary: 'cyan',
      tagline: 'Enterprise orchestration with app-scoped controls.',
      logo_alt: 'Mozaiks',
    },
    admin: {
      enabled: true,
      admins: ['developer@mozaiks.local'],
    },
    shell: {
      header_page_count: 6,
      header_action_count: 0,
    },
    workspace: {
      workflow_count: workflows.length,
      workflow_names: workflows,
      entry_point: workflows[0] || null,
      runtime_readiness: workflows.length > 0 ? 'entry_point_configured' : 'no_workflows',
    },
    home: {
      next_step:
        app?.status === 'active'
          ? 'Use the app Studio to review runtime health, revenue, integrations, and operations.'
          : app?.status === 'deploying'
            ? 'Complete deployment checks, then hand this app into live operation.'
            : 'Continue Build to move this app toward its next lifecycle gate.',
    },
  }
}

export function buildStudioDemoRuntimeSummary() {
  return JSON.parse(JSON.stringify({
    ...DEMO_RUNTIME_SUMMARY,
    runtime_integrations: DEMO_RUNTIME_SUMMARY.integrations,
    app_connectors: [],
    connector_summary: {},
  }))
}

export function listStudioDemoAppConnectors(appId) {
  return (DEMO_APP_CONNECTORS[appId] || []).map((connector) => ({ ...connector }))
}

export function getStudioDemoUsageRecord(appId) {
  const usage = getStudioDemoUsagePayload(appId)
  const rows = usage.by_run || []
  if (!usage || rows.length === 0) {
    const record = DEMO_USAGE_BY_APP[appId]
    return record ? { ...record } : null
  }
  return {
    tokens_used: usage.totals.total_tokens,
    llm_cost_usd: usage.totals.estimated_cost_usd,
    workflow_runs: rows.length,
    tool_calls: rows.reduce((total, row) => total + Number(row.tool_calls || 0), 0),
    errors: rows.reduce((total, row) => total + Number(row.errors || 0), 0),
  }
}

export function getStudioDemoBillingRecord(appId) {
  const record = DEMO_BILLING_BY_APP[appId]
  return record ? { ...record } : null
}

export function getStudioDemoDeploymentRecord(appId) {
  const record = DEMO_DEPLOYMENT_BY_APP[appId]
  return record ? { ...record, domains: [...(record.domains || [])] } : null
}

export function getStudioDemoUsersRecord(appId) {
  const record = DEMO_USERS_BY_APP[appId]
  return record
    ? {
        ...record,
        users: (record.users || []).map((user) => ({ ...user })),
        segments: (record.segments || []).map((segment) => ({ ...segment })),
        support_history: (record.support_history || []).map((entry) => ({ ...entry })),
      }
    : null
}

export function getStudioDemoActivity(appId) {
  return (DEMO_ACTIVITY_BY_APP[appId] || []).map((entry) => ({ ...entry }))
}

export function getStudioDemoWorkflowNames(appId) {
  return [...(DEMO_WORKFLOWS_BY_APP[appId] || [])]
}

export function getStudioDemoRuns(appId) {
  const usage = getStudioDemoUsagePayload(appId)
  if (usage.by_run.length > 0) {
    return usage.by_run.slice(0, 12).map((run) => ({ ...run }))
  }
  return (DEMO_RUNS_BY_APP[appId] || []).map((run) => ({ ...run }))
}

export function getStudioDemoSessions(appId) {
  return (DEMO_SESSIONS_BY_APP[appId] || []).map((session) => ({ ...session }))
}

export function getStudioDemoBuildHistory(appId) {
  return {
    artifact_versions: (DEMO_BUILD_HISTORY_BY_APP[appId] || []).map((version) => ({
      ...version,
      commit_metadata: JSON.parse(JSON.stringify(version.commit_metadata || {})),
    })),
    change_requests: [],
  }
}

export function getStudioDemoAdminStats(appId) {
  const usagePayload = getStudioDemoUsagePayload(appId)
  const rows = usagePayload.by_run || []
  if (rows.length === 0) {
    return {
      active_chats: 0,
      tracked_chats: 0,
      total_agent_turns: 0,
      total_tool_calls: 0,
      total_errors: 0,
      total_prompt_tokens: 0,
      total_completion_tokens: 0,
      total_cost: 0,
    }
  }
  return {
    active_chats: rows.filter((row) => !row.ended_at).length,
    tracked_chats: rows.length,
    total_agent_turns: rows.reduce((total, row) => total + Number(row.agent_turns || 0), 0),
    total_tool_calls: rows.reduce((total, row) => total + Number(row.tool_calls || 0), 0),
    total_errors: rows.reduce((total, row) => total + Number(row.errors || 0), 0),
    total_prompt_tokens: usagePayload.totals.prompt_tokens,
    total_completion_tokens: usagePayload.totals.completion_tokens,
    total_cost: usagePayload.totals.estimated_cost_usd,
  }
}

export function getStudioDemoWorkspaceRuns() {
  return getStudioDemoWorkspaceUsage().by_run.map((run) => ({ ...run }))
}

export function getStudioDemoWorkspaceStats() {
  const usage = getStudioDemoWorkspaceUsage()
  const runs = usage.by_run

  return {
    active_chats: runs.filter((run) => !run.ended_at).length,
    tracked_chats: runs.length,
    total_agent_turns: runs.reduce((total, run) => total + Number(run.agent_turns || 0), 0),
    total_tool_calls: runs.reduce((total, run) => total + Number(run.tool_calls || 0), 0),
    total_errors: runs.reduce((total, run) => total + Number(run.errors || 0), 0),
    total_prompt_tokens: usage.totals.prompt_tokens,
    total_completion_tokens: usage.totals.completion_tokens,
    total_cost: usage.totals.estimated_cost_usd,
  }
}

export function getStudioDemoUsagePayload(appId) {
  return summarizeDemoUsageRows(buildDemoUsageRows(appId), appId)
}

export function getStudioDemoWorkspaceUsage() {
  return summarizeDemoUsageRows(buildDemoUsageRows(), null)
}

export default buildStudioDemoApps
