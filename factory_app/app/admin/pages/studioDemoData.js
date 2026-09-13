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

// ─── Demo analytics (portfolio / app performance surfaces) ───────────────────
//
// Deterministic demo payloads mirroring the /api/studio/analytics/* shape.
// The registry block mirrors the display metadata of the OSS default metric
// registry (mozaiksai.core.metrics.definitions) for demo rendering only —
// live mode always uses the backend-served registry.

const DEMO_METRIC_REGISTRY = {
  mrr: { metric_id: 'mrr', domain: 'revenue', label: 'MRR', short_label: 'MRR', unit: 'currency_usd', polarity: 'higher_is_better', source: 'kpi_snapshot', temporality: 'stock', benchmarkable: true, headline: true, description: 'Monthly recurring revenue.', related: ['arr', 'mrr_growth', 'net_new_mrr', 'arppu'] },
  arr: { metric_id: 'arr', domain: 'revenue', label: 'ARR', short_label: 'ARR', unit: 'currency_usd', polarity: 'higher_is_better', source: 'derived', temporality: 'stock', description: 'Annualized recurring revenue (MRR x 12).', related: ['mrr'] },
  new_mrr: { metric_id: 'new_mrr', domain: 'revenue', label: 'New MRR', short_label: 'New MRR', unit: 'currency_usd', polarity: 'higher_is_better', source: 'kpi_snapshot', temporality: 'flow', description: 'Recurring revenue added by new subscriptions.', related: ['net_new_mrr'] },
  expansion_mrr: { metric_id: 'expansion_mrr', domain: 'revenue', label: 'Expansion MRR', short_label: 'Expansion', unit: 'currency_usd', polarity: 'higher_is_better', source: 'kpi_snapshot', temporality: 'flow', description: 'Revenue added by upgrades.', related: ['net_new_mrr'] },
  contraction_mrr: { metric_id: 'contraction_mrr', domain: 'revenue', label: 'Contraction MRR', short_label: 'Contraction', unit: 'currency_usd', polarity: 'lower_is_better', source: 'kpi_snapshot', temporality: 'flow', description: 'Revenue lost to downgrades.', related: ['net_new_mrr'] },
  churned_mrr: { metric_id: 'churned_mrr', domain: 'revenue', label: 'Churned MRR', short_label: 'Churned', unit: 'currency_usd', polarity: 'lower_is_better', source: 'kpi_snapshot', temporality: 'flow', description: 'Revenue lost to cancellations.', related: ['net_new_mrr'] },
  net_new_mrr: { metric_id: 'net_new_mrr', domain: 'revenue', label: 'Net New MRR', short_label: 'Net New MRR', unit: 'currency_usd', polarity: 'higher_is_better', source: 'derived', temporality: 'flow', headline: true, description: 'New + expansion − contraction − churned MRR.', related: ['new_mrr', 'expansion_mrr', 'contraction_mrr', 'churned_mrr'] },
  mrr_growth: { metric_id: 'mrr_growth', domain: 'revenue', label: 'MRR growth', short_label: 'Growth', unit: 'percent', polarity: 'higher_is_better', source: 'derived', temporality: 'stock', benchmarkable: true, headline: true, description: 'Percent change in MRR versus the comparison period.', related: ['mrr'] },
  nrr: { metric_id: 'nrr', domain: 'revenue', label: 'NRR', short_label: 'NRR', unit: 'percent', polarity: 'higher_is_better', source: 'derived', temporality: 'flow', benchmarkable: true, headline: true, description: 'Net revenue retention from portfolio totals.', related: ['grr'] },
  grr: { metric_id: 'grr', domain: 'revenue', label: 'GRR', short_label: 'GRR', unit: 'percent', polarity: 'higher_is_better', source: 'derived', temporality: 'flow', description: 'Gross revenue retention from portfolio totals.', related: ['nrr'] },
  arpu: { metric_id: 'arpu', domain: 'revenue', label: 'ARPU', short_label: 'ARPU', unit: 'currency_usd', polarity: 'higher_is_better', source: 'derived', temporality: 'stock', description: 'MRR per active user.', related: ['arppu'] },
  arppu: { metric_id: 'arppu', domain: 'revenue', label: 'ARPPU', short_label: 'ARPPU', unit: 'currency_usd', polarity: 'higher_is_better', source: 'derived', temporality: 'stock', description: 'MRR per paying user.', related: ['arpu'] },
  active_users: { metric_id: 'active_users', domain: 'users', label: 'Active users', short_label: 'Active', unit: 'count', polarity: 'higher_is_better', source: 'usage_rollup', temporality: 'stock', benchmarkable: true, headline: true, description: 'Distinct users with recorded activity in the period.', related: ['total_users'] },
  total_users: { metric_id: 'total_users', domain: 'users', label: 'Total users', short_label: 'Total', unit: 'count', polarity: 'higher_is_better', source: 'kpi_snapshot', temporality: 'stock', description: 'All registered users.', related: ['new_users'] },
  new_users: { metric_id: 'new_users', domain: 'users', label: 'New users', short_label: 'New', unit: 'count', polarity: 'higher_is_better', source: 'kpi_snapshot', temporality: 'flow', description: 'Users who signed up during the period.', related: ['total_users'] },
  paying_users: { metric_id: 'paying_users', domain: 'users', label: 'Paying users', short_label: 'Paying', unit: 'count', polarity: 'higher_is_better', source: 'kpi_snapshot', temporality: 'stock', benchmarkable: true, headline: true, description: 'Users with an active paid subscription.', related: ['paid_conversion'] },
  churned_users: { metric_id: 'churned_users', domain: 'users', label: 'Churned users', short_label: 'Churned', unit: 'count', polarity: 'lower_is_better', source: 'kpi_snapshot', temporality: 'flow', description: 'Users who cancelled or lapsed during the period.', related: ['churn_rate'] },
  user_growth: { metric_id: 'user_growth', domain: 'users', label: 'User growth', short_label: 'Growth', unit: 'percent', polarity: 'higher_is_better', source: 'derived', temporality: 'stock', benchmarkable: true, headline: true, description: 'Percent change in active users versus the comparison period.', related: ['active_users'] },
  paid_conversion: { metric_id: 'paid_conversion', domain: 'users', label: 'Paid conversion', short_label: 'Conversion', unit: 'percent', polarity: 'higher_is_better', source: 'derived', temporality: 'stock', benchmarkable: true, headline: true, description: 'Paying users as a share of total users.', related: ['paying_users', 'total_users'] },
  churn_rate: { metric_id: 'churn_rate', domain: 'users', label: 'Churn', short_label: 'Churn', unit: 'percent', polarity: 'lower_is_better', source: 'derived', temporality: 'flow', benchmarkable: true, description: 'Churned users as a share of starting total users.', related: ['retention'] },
  retention: { metric_id: 'retention', domain: 'users', label: 'Retention', short_label: 'Retention', unit: 'percent', polarity: 'higher_is_better', source: 'derived', temporality: 'flow', benchmarkable: true, description: '100% minus the period churn rate.', related: ['churn_rate'] },
}

// Base signals per demo app: current and comparison-period levels plus the
// period's MRR movement components. Campaign workbench intentionally declines
// so the attention section has an honest demo story.
const DEMO_ANALYTICS_BY_APP = {
  'partner-delivery-studio': {
    mrr: 9200, prev_mrr: 8760, starting_mrr: 8760,
    new_mrr: 520, expansion_mrr: 240, contraction_mrr: 120, churned_mrr: 200,
    prev_new_mrr: 480, prev_expansion_mrr: 210, prev_contraction_mrr: 140, prev_churned_mrr: 260,
    paying_users: 54, prev_paying_users: 51,
    total_users: 204, prev_total_users: 189,
    new_users: 21, prev_new_users: 18,
    churned_users: 8, prev_churned_users: 6,
    active_users: 163, prev_active_users: 149,
  },
  'member-growth-studio': {
    mrr: 27800, prev_mrr: 25300, starting_mrr: 25300,
    new_mrr: 2100, expansion_mrr: 900, contraction_mrr: 180, churned_mrr: 320,
    prev_new_mrr: 1750, prev_expansion_mrr: 640, prev_contraction_mrr: 220, prev_churned_mrr: 410,
    paying_users: 482, prev_paying_users: 448,
    total_users: 2480, prev_total_users: 2308,
    new_users: 206, prev_new_users: 168,
    churned_users: 74, prev_churned_users: 58,
    active_users: 1824, prev_active_users: 1698,
  },
  'campaign-revision-workbench': {
    mrr: 1600, prev_mrr: 1750, starting_mrr: 1750,
    new_mrr: 40, expansion_mrr: 0, contraction_mrr: 60, churned_mrr: 130,
    prev_new_mrr: 120, prev_expansion_mrr: 30, prev_contraction_mrr: 40, prev_churned_mrr: 70,
    paying_users: 12, prev_paying_users: 14,
    total_users: 19, prev_total_users: 18,
    new_users: 2, prev_new_users: 4,
    churned_users: 1, prev_churned_users: 1,
    active_users: 11, prev_active_users: 14,
  },
}

const DEMO_FUNNEL_BY_APP = {
  'partner-delivery-studio': {
    configured: true, available: true, funnel_id: 'partner_lifecycle', label: 'Partner lifecycle', subject: 'actor',
    steps: [
      { step_id: 'signed_up', label: 'Signed up', event_name: 'user.signed_up', count: 204, conversion_rate: null },
      { step_id: 'connected_delivery', label: 'Connected delivery', event_name: 'delivery.connected', count: 151, conversion_rate: 74.02 },
      { step_id: 'first_release', label: 'Shipped a release', event_name: 'release.shipped', count: 96, conversion_rate: 63.58 },
      { step_id: 'paid', label: 'Paid', event_name: 'subscription.activated', count: 54, conversion_rate: 56.25 },
    ],
  },
  'member-growth-studio': {
    configured: true, available: true, funnel_id: 'growth_lifecycle', label: 'Growth lifecycle', subject: 'actor',
    steps: [
      { step_id: 'signed_up', label: 'Signed up', event_name: 'user.signed_up', count: 2480, conversion_rate: null },
      { step_id: 'launched_campaign', label: 'Launched a campaign', event_name: 'campaign.launched', count: 1533, conversion_rate: 61.81 },
      { step_id: 'invited_team', label: 'Invited team', event_name: 'team.invited', count: 918, conversion_rate: 59.88 },
      { step_id: 'paid', label: 'Paid', event_name: 'subscription.activated', count: 482, conversion_rate: 52.51 },
      { step_id: 'retained', label: 'Retained 60 days', event_name: 'subscription.renewed', count: 401, conversion_rate: 83.2 },
    ],
  },
  'campaign-revision-workbench': { configured: false },
}

function demoEnvelope(value, previous) {
  const hasBoth = value != null && previous != null
  return {
    value: value ?? null,
    previous: previous ?? null,
    delta: hasBoth ? value - previous : null,
    delta_pct: hasBoth && previous !== 0 ? ((value - previous) / Math.abs(previous)) * 100 : null,
    available: value != null,
  }
}

function demoGrowthEnvelope(current, previous) {
  const value = current != null && previous ? ((current - previous) / Math.abs(previous)) * 100 : null
  return { value, previous: null, delta: null, delta_pct: null, available: value != null }
}

function buildDemoMetricValues(base) {
  const netNew = base.new_mrr + base.expansion_mrr - base.contraction_mrr - base.churned_mrr
  const prevNetNew = base.prev_new_mrr + base.prev_expansion_mrr - base.prev_contraction_mrr - base.prev_churned_mrr
  const nrr = base.starting_mrr
    ? ((base.starting_mrr + base.expansion_mrr - base.contraction_mrr - base.churned_mrr) / base.starting_mrr) * 100
    : null
  const grr = base.starting_mrr
    ? ((base.starting_mrr - base.contraction_mrr - base.churned_mrr) / base.starting_mrr) * 100
    : null
  const churnRate = base.total_users ? (base.churned_users / base.total_users) * 100 : null
  const prevChurnRate = base.prev_total_users ? (base.prev_churned_users / base.prev_total_users) * 100 : null
  return {
    mrr: demoEnvelope(base.mrr, base.prev_mrr),
    arr: demoEnvelope(base.mrr * 12, base.prev_mrr * 12),
    new_mrr: demoEnvelope(base.new_mrr, base.prev_new_mrr),
    expansion_mrr: demoEnvelope(base.expansion_mrr, base.prev_expansion_mrr),
    contraction_mrr: demoEnvelope(base.contraction_mrr, base.prev_contraction_mrr),
    churned_mrr: demoEnvelope(base.churned_mrr, base.prev_churned_mrr),
    net_new_mrr: demoEnvelope(netNew, prevNetNew),
    mrr_growth: demoGrowthEnvelope(base.mrr, base.prev_mrr),
    nrr: demoEnvelope(nrr, null),
    grr: demoEnvelope(grr, null),
    arpu: demoEnvelope(base.active_users ? base.mrr / base.active_users : null, base.prev_active_users ? base.prev_mrr / base.prev_active_users : null),
    arppu: demoEnvelope(base.paying_users ? base.mrr / base.paying_users : null, base.prev_paying_users ? base.prev_mrr / base.prev_paying_users : null),
    active_users: demoEnvelope(base.active_users, base.prev_active_users),
    total_users: demoEnvelope(base.total_users, base.prev_total_users),
    new_users: demoEnvelope(base.new_users, base.prev_new_users),
    paying_users: demoEnvelope(base.paying_users, base.prev_paying_users),
    churned_users: demoEnvelope(base.churned_users, base.prev_churned_users),
    user_growth: demoGrowthEnvelope(base.active_users, base.prev_active_users),
    paid_conversion: demoEnvelope(
      base.total_users ? (base.paying_users / base.total_users) * 100 : null,
      base.prev_total_users ? (base.prev_paying_users / base.prev_total_users) * 100 : null,
    ),
    churn_rate: demoEnvelope(churnRate, prevChurnRate),
    retention: demoEnvelope(churnRate == null ? null : 100 - churnRate, prevChurnRate == null ? null : 100 - prevChurnRate),
  }
}

function buildDemoSeries(previous, current, days = 21, wobbleScale = 0.03) {
  const points = []
  for (let index = 0; index < days; index += 1) {
    const progress = days <= 1 ? 1 : index / (days - 1)
    const wobble = Math.sin(index * 1.7) * wobbleScale * Math.max(Math.abs(current), 1)
    const value = previous + (current - previous) * progress + (index === days - 1 ? 0 : wobble)
    points.push({
      period_start: addUtcDays(DEMO_USAGE_BASE_DATE, index - (days - 1)).toISOString().slice(0, 10),
      value: roundMoney(Math.max(0, value)),
    })
  }
  return points
}

function buildDemoAppSeries(base) {
  return {
    mrr: buildDemoSeries(base.prev_mrr, base.mrr),
    arr: buildDemoSeries(base.prev_mrr * 12, base.mrr * 12),
    net_new_mrr: buildDemoSeries(
      (base.prev_new_mrr + base.prev_expansion_mrr - base.prev_contraction_mrr - base.prev_churned_mrr) / 21,
      (base.new_mrr + base.expansion_mrr - base.contraction_mrr - base.churned_mrr) / 21,
      21,
      0.25,
    ),
    active_users: buildDemoSeries(base.prev_active_users, base.active_users, 21, 0.05),
    paying_users: buildDemoSeries(base.prev_paying_users, base.paying_users, 21, 0.01),
    new_users: buildDemoSeries(base.prev_new_users / 21, base.new_users / 21, 21, 0.4),
  }
}

function sumDemoSeries(seriesList) {
  const buckets = new Map()
  for (const series of seriesList) {
    for (const point of series || []) {
      buckets.set(point.period_start, (buckets.get(point.period_start) || 0) + point.value)
    }
  }
  return Array.from(buckets.entries())
    .sort((left, right) => left[0].localeCompare(right[0]))
    .map(([period_start, value]) => ({ period_start, value: roundMoney(value) }))
}

function demoPeriodPayload(periodId) {
  const days = periodId === '7d' ? -7 : periodId === '90d' ? -90 : -30
  const until = DEMO_USAGE_BASE_DATE
  const since = addUtcDays(until, days).toISOString()
  const labels = { '7d': 'Last 7 days', '30d': 'Last 30 days', '90d': 'Last 90 days' }
  const comparisons = { '7d': 'vs previous 7 days', '30d': 'vs previous 30 days', '90d': 'vs previous 90 days' }
  return {
    id: periodId,
    label: labels[periodId] || 'Last 30 days',
    comparison_label: comparisons[periodId] || 'vs previous 30 days',
    since,
    until,
    previous_since: addUtcDays(since, days).toISOString(),
    previous_until: since,
  }
}

const DEMO_PORTFOLIO_INSIGHTS = [
  {
    insight_id: 'mrr_decline', severity: 'attention', app_id: 'campaign-revision-workbench',
    app_name: 'Campaign Revision Workbench', metric_id: 'mrr',
    headline: 'Campaign Revision Workbench MRR declined 8.6%',
    detail: 'Largest revenue decline in the portfolio this period.', delta_pct: -8.57, delta: null,
  },
  {
    insight_id: 'conversion_drop', severity: 'attention', app_id: 'campaign-revision-workbench',
    app_name: 'Campaign Revision Workbench', metric_id: 'paid_conversion',
    headline: 'Campaign Revision Workbench paid conversion declined',
    detail: 'Paid conversion fell 14.6 points this period.', delta: -14.6, delta_pct: null,
  },
  {
    insight_id: 'mrr_growth_leader', severity: 'highlight', app_id: 'member-growth-studio',
    app_name: 'Member Growth Studio', metric_id: 'mrr',
    headline: 'Member Growth Studio is the fastest-growing app',
    detail: 'MRR grew 9.9% versus the comparison period.', delta_pct: 9.88, delta: null,
  },
]

function buildDemoPortfolioBase() {
  const bases = Object.values(DEMO_ANALYTICS_BY_APP)
  const summed = {}
  const keys = Object.keys(bases[0])
  for (const key of keys) {
    summed[key] = bases.reduce((total, base) => total + Number(base[key] || 0), 0)
  }
  return summed
}

export function getStudioDemoAnalyticsPortfolio(periodId = '30d') {
  const portfolioBase = buildDemoPortfolioBase()
  const apps = DEMO_APPS.map((app) => ({
    app_id: app.app_id,
    name: app.name,
    lifecycle_state: app.status,
    metrics: buildDemoMetricValues(DEMO_ANALYTICS_BY_APP[app.app_id]),
    error: false,
  }))
  const seriesByApp = DEMO_APPS.map((app) => buildDemoAppSeries(DEMO_ANALYTICS_BY_APP[app.app_id]))
  const series = {}
  for (const metricId of ['mrr', 'arr', 'net_new_mrr', 'active_users', 'paying_users', 'new_users']) {
    series[metricId] = sumDemoSeries(seriesByApp.map((appSeries) => appSeries[metricId]))
  }
  const medians = (metricId) => {
    const values = apps.map((app) => app.metrics[metricId]?.value).filter((value) => value != null).sort((a, b) => a - b)
    if (!values.length) return null
    const mid = Math.floor(values.length / 2)
    return values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2
  }
  const benchmarks = {}
  for (const metricId of ['mrr', 'mrr_growth', 'nrr', 'active_users', 'paying_users', 'user_growth', 'paid_conversion', 'churn_rate', 'retention']) {
    benchmarks[metricId] = { median: medians(metricId), sample_size: apps.length }
  }
  return {
    period: demoPeriodPayload(periodId),
    registry: DEMO_METRIC_REGISTRY,
    portfolio: buildDemoMetricValues(portfolioBase),
    series,
    apps,
    benchmarks,
    insights: DEMO_PORTFOLIO_INSIGHTS,
    availability: { revenue: 'full', users: 'full' },
  }
}

export function getStudioDemoAnalyticsApp(appId, periodId = '30d') {
  const base = DEMO_ANALYTICS_BY_APP[appId] || DEMO_ANALYTICS_BY_APP[DEMO_APPS[0].app_id]
  const app = DEMO_APPS.find((entry) => entry.app_id === appId) || DEMO_APPS[0]
  const values = buildDemoMetricValues(base)
  return {
    period: demoPeriodPayload(periodId),
    registry: DEMO_METRIC_REGISTRY,
    app: { app_id: app.app_id, name: app.name, lifecycle_state: app.status },
    metrics: values,
    series: buildDemoAppSeries(base),
    movement: {
      available: true,
      starting_mrr: base.starting_mrr,
      ending_mrr: base.mrr,
      unexplained: null,
      new_mrr: base.new_mrr,
      expansion_mrr: base.expansion_mrr,
      contraction_mrr: base.contraction_mrr,
      churned_mrr: base.churned_mrr,
    },
    funnel: DEMO_FUNNEL_BY_APP[app.app_id] || { configured: false },
    insights: DEMO_PORTFOLIO_INSIGHTS.filter((insight) => insight.app_id === app.app_id),
    availability: { revenue: 'full', users: 'full' },
    error: false,
  }
}

export function getStudioDemoMetricDetail(appId, metricId, periodId = '30d') {
  const payload = getStudioDemoAnalyticsApp(appId, periodId)
  const definition = DEMO_METRIC_REGISTRY[metricId] || null
  const portfolio = getStudioDemoAnalyticsPortfolio(periodId)
  return {
    period: payload.period,
    definition,
    app: { app_id: payload.app.app_id, name: payload.app.name },
    value: payload.metrics[metricId] || null,
    series: payload.series[metricId] || [],
    drivers: (definition?.related || [])
      .filter((relatedId) => ['new_mrr', 'expansion_mrr', 'contraction_mrr', 'churned_mrr'].includes(relatedId))
      .map((relatedId) => (payload.metrics[relatedId] ? { metric_id: relatedId, ...payload.metrics[relatedId] } : null))
      .filter(Boolean),
    related: (definition?.related || [])
      .map((relatedId) => (payload.metrics[relatedId] ? { metric_id: relatedId, ...payload.metrics[relatedId] } : null))
      .filter(Boolean),
    benchmark: definition?.benchmarkable
      ? { kind: 'portfolio_median', label: 'Portfolio median', median: portfolio.benchmarks[metricId]?.median ?? null, sample_size: 3 }
      : null,
    error: false,
  }
}

export default buildStudioDemoApps
