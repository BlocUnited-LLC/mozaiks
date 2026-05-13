const DEMO_APPS = [
  {
    build_registry_id: 'demo_appreg_client-intake',
    app_id: 'client-intake-copilot',
    name: 'Client Intake Copilot',
    description: 'Intake routing, follow-up tasks, and approval capture.',
    status: 'draft',
    created_at: '2025-02-02T10:00:00Z',
    updated_at: '2025-02-02T10:00:00Z',
  },
  {
    build_registry_id: 'demo_appreg_support-ops',
    app_id: 'support-ops-assistant',
    name: 'Support Ops Assistant',
    description: 'Ticket triage, escalation classification, and runbook prompts.',
    status: 'building',
    created_at: '2025-02-01T12:30:00Z',
    updated_at: '2025-02-03T09:15:00Z',
  },
  {
    build_registry_id: 'demo_appreg_revenue-review',
    app_id: 'revenue-review-console',
    name: 'Revenue Review Console',
    description: 'Finance review before release and deployment setup.',
    status: 'review',
    created_at: '2025-01-27T15:45:00Z',
    updated_at: '2025-02-04T14:05:00Z',
  },
  {
    build_registry_id: 'demo_appreg_partner-deploy',
    app_id: 'partner-delivery-console',
    name: 'Partner Delivery Console',
    description: 'Partner rollout, managed deployment, and release checks.',
    status: 'deploying',
    created_at: '2025-01-19T08:00:00Z',
    updated_at: '2025-02-05T11:20:00Z',
  },
  {
    build_registry_id: 'demo_appreg_member-growth',
    app_id: 'member-growth-console',
    name: 'Member Growth Console',
    description: 'Live growth insights, campaign prompts, and operator alerts.',
    status: 'active',
    created_at: '2025-01-10T13:10:00Z',
    updated_at: '2025-02-05T16:40:00Z',
  },
  {
    build_registry_id: 'demo_appreg_campaign-revision',
    app_id: 'campaign-revision-workbench',
    name: 'Campaign Revision Workbench',
    description: 'Revision work is blocking the next approved release.',
    status: 'needs_revision',
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
      primary_model: 'gpt-4o-mini',
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
  'member-growth-console': [
    { service: 'stripe', display_name: 'Stripe', secret_available: true, status: 'active' },
    { service: 'slack', display_name: 'Slack', secret_available: true, status: 'active' },
  ],
  'support-ops-assistant': [
    { service: 'salesforce', display_name: 'Salesforce', secret_available: true, status: 'active' },
  ],
  'revenue-review-console': [
    { service: 'stripe', display_name: 'Stripe', secret_available: false, status: 'metadata_only' },
  ],
  'campaign-revision-workbench': [
    { service: 'mailchimp', display_name: 'Mailchimp', secret_available: false, status: 'metadata_only' },
  ],
}

const DEMO_USAGE_BY_APP = {
  'client-intake-copilot': {
    tokens_used: 0,
    llm_cost_usd: 0,
    workflow_runs: 0,
    tool_calls: 0,
    errors: 0,
  },
  'support-ops-assistant': {
    tokens_used: 58200,
    llm_cost_usd: 141.12,
    workflow_runs: 810,
    tool_calls: 2710,
    errors: 3,
  },
  'revenue-review-console': {
    tokens_used: 126000,
    llm_cost_usd: 301.22,
    workflow_runs: 1400,
    tool_calls: 5230,
    errors: 1,
  },
  'partner-delivery-console': {
    tokens_used: 348000,
    llm_cost_usd: 912.4,
    workflow_runs: 4210,
    tool_calls: 13220,
    errors: 4,
  },
  'member-growth-console': {
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
  'client-intake-copilot': {
    total_revenue_usd: 0,
    mrr_usd: 0,
    arr_usd: 0,
    active_customers: 0,
    failed_payments: 0,
  },
  'support-ops-assistant': {
    total_revenue_usd: 12800,
    mrr_usd: 2200,
    arr_usd: 26400,
    active_customers: 18,
    failed_payments: 1,
  },
  'revenue-review-console': {
    total_revenue_usd: 24800,
    mrr_usd: 4100,
    arr_usd: 49200,
    active_customers: 26,
    failed_payments: 2,
  },
  'partner-delivery-console': {
    total_revenue_usd: 52400,
    mrr_usd: 9200,
    arr_usd: 110400,
    active_customers: 54,
    failed_payments: 1,
  },
  'member-growth-console': {
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
  'client-intake-copilot': {
    deployment_label: 'Draft',
    failed: false,
    domain_count: 0,
    domains: [],
    environment: 'Build',
    bandwidth_gb: 0,
    storage_gb: 0,
    uptime_percent: null,
  },
  'support-ops-assistant': {
    deployment_label: 'Build queue',
    failed: false,
    domain_count: 0,
    domains: [],
    environment: 'Build',
    bandwidth_gb: 0,
    storage_gb: 2,
    uptime_percent: null,
  },
  'revenue-review-console': {
    deployment_label: 'Review',
    failed: false,
    domain_count: 0,
    domains: [],
    environment: 'Staging planned',
    bandwidth_gb: 0,
    storage_gb: 4,
    uptime_percent: null,
  },
  'partner-delivery-console': {
    deployment_label: 'Deploying',
    failed: false,
    domain_count: 1,
    domains: ['partner-console-preview.mozaiks.app'],
    environment: 'Staging',
    bandwidth_gb: 180,
    storage_gb: 22,
    uptime_percent: 99.3,
  },
  'member-growth-console': {
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
    deployment_label: 'Rollback required',
    failed: true,
    domain_count: 0,
    domains: [],
    environment: 'Pre-deploy',
    bandwidth_gb: 0,
    storage_gb: 5,
    uptime_percent: null,
  },
}

const DEMO_USERS_BY_APP = {
  'client-intake-copilot': {
    total_users: 0,
    active_users: 0,
    new_users: 0,
    churned_users: 0,
    users: [],
    segments: [],
    support_history: [],
  },
  'support-ops-assistant': {
    total_users: 118,
    active_users: 83,
    new_users: 14,
    churned_users: 6,
    users: [
      { id: 'usr_sop_1', name: 'Tanya Brooks', email: 'tanya@example.com', segment: 'Ops leads', status: 'active', subscription: 'Growth', last_seen: '2 hours ago' },
      { id: 'usr_sop_2', name: 'Luis Carter', email: 'luis@example.com', segment: 'Supervisors', status: 'active', subscription: 'Growth', last_seen: '6 hours ago' },
      { id: 'usr_sop_3', name: 'Jade Prince', email: 'jade@example.com', segment: 'Escalation team', status: 'inactive', subscription: 'Starter', last_seen: '4 days ago' },
    ],
    segments: [
      { label: 'Ops leads', count: 28 },
      { label: 'Supervisors', count: 41 },
      { label: 'Escalation team', count: 19 },
    ],
    support_history: [
      { id: 'sup_sop_1', label: 'SLA escalation', detail: '2 unresolved support escalations in the last 7 days.' },
    ],
  },
  'revenue-review-console': {
    total_users: 64,
    active_users: 37,
    new_users: 5,
    churned_users: 3,
    users: [
      { id: 'usr_rev_1', name: 'Kelsey Ward', email: 'kelsey@example.com', segment: 'Finance operators', status: 'active', subscription: 'Pro', last_seen: '1 hour ago' },
      { id: 'usr_rev_2', name: 'Milo Hart', email: 'milo@example.com', segment: 'Auditors', status: 'active', subscription: 'Enterprise', last_seen: 'Today' },
    ],
    segments: [
      { label: 'Finance operators', count: 31 },
      { label: 'Auditors', count: 12 },
      { label: 'Reviewers', count: 21 },
    ],
    support_history: [
      { id: 'sup_rev_1', label: 'Billing inquiry', detail: '1 plan question waiting on finance configuration.' },
    ],
  },
  'partner-delivery-console': {
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
  'member-growth-console': {
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
  'client-intake-copilot': [
    { id: 'act_client_1', title: 'Draft created', detail: 'Initial app record saved and ready for Build.', timestamp: 'Just now' },
  ],
  'support-ops-assistant': [
    { id: 'act_support_1', title: 'Build brief updated', detail: 'Escalation flow changes were saved to the current Build request.', timestamp: '1 hour ago' },
    { id: 'act_support_2', title: 'Validation ready', detail: 'Build output is ready for review before deploy.', timestamp: 'Yesterday' },
  ],
  'revenue-review-console': [
    { id: 'act_revenue_1', title: 'Review requested', detail: 'Finance release review is waiting on operator approval.', timestamp: '34 minutes ago' },
  ],
  'partner-delivery-console': [
    { id: 'act_partner_1', title: 'Preview deployment started', detail: 'Deployment is provisioning the current release candidate.', timestamp: '18 minutes ago' },
    { id: 'act_partner_2', title: 'Domain assigned', detail: 'Preview domain attached to the latest environment.', timestamp: 'Today' },
  ],
  'member-growth-console': [
    { id: 'act_growth_1', title: 'Usage spike detected', detail: 'Campaign scoring workflows crossed the daily run threshold.', timestamp: '12 minutes ago' },
    { id: 'act_growth_2', title: 'Customer billing synced', detail: 'Billing data refreshed from the managed billing integration.', timestamp: '1 hour ago' },
  ],
  'campaign-revision-workbench': [
    { id: 'act_campaign_1', title: 'Revision requested', detail: 'Rollback decision recorded after the latest review pass.', timestamp: '2 hours ago' },
  ],
}

const DEMO_WORKFLOWS_BY_APP = {
  'client-intake-copilot': ['ValueEngine'],
  'support-ops-assistant': ['AppGenerator', 'TicketTriageWorkflow', 'EscalationWorkflow'],
  'revenue-review-console': ['ReviewWorkflow', 'ReportsWorkflow'],
  'partner-delivery-console': ['DeployWorkflow', 'PartnerSyncWorkflow', 'PayoutOpsWorkflow'],
  'member-growth-console': ['GrowthScoringWorkflow', 'CampaignReviewWorkflow', 'RetentionSignalsWorkflow'],
  'campaign-revision-workbench': ['RevisionWorkflow'],
}

const DEMO_RUNS_BY_APP = {
  'support-ops-assistant': [
    { chat_id: 'run_support_1', workflow_name: 'TicketTriageWorkflow', user_id: 'tanya@example.com', agent_turns: 42, tool_calls: 184, errors: 1, prompt_tokens: 12400, completion_tokens: 3800, cost: 32.1, runtime_sec: 182.3, ended_at: null, started_at: '2025-02-05T17:20:00Z', status: 0 },
    { chat_id: 'run_support_2', workflow_name: 'EscalationWorkflow', user_id: 'luis@example.com', agent_turns: 18, tool_calls: 61, errors: 0, prompt_tokens: 6400, completion_tokens: 2200, cost: 16.7, runtime_sec: 94.2, ended_at: '2025-02-05T16:10:00Z', started_at: '2025-02-05T15:58:00Z', status: 1 },
  ],
  'revenue-review-console': [
    { chat_id: 'run_revenue_1', workflow_name: 'ReviewWorkflow', user_id: 'kelsey@example.com', agent_turns: 14, tool_calls: 44, errors: 1, prompt_tokens: 9200, completion_tokens: 2800, cost: 21.6, runtime_sec: 75.8, ended_at: '2025-02-05T15:42:00Z', started_at: '2025-02-05T15:30:00Z', status: 1 },
  ],
  'partner-delivery-console': [
    { chat_id: 'run_partner_1', workflow_name: 'DeployWorkflow', user_id: 'mina@example.com', agent_turns: 22, tool_calls: 73, errors: 0, prompt_tokens: 13200, completion_tokens: 4100, cost: 28.9, runtime_sec: 133.4, ended_at: null, started_at: '2025-02-05T18:02:00Z', status: 0 },
    { chat_id: 'run_partner_2', workflow_name: 'PartnerSyncWorkflow', user_id: 'devin@example.com', agent_turns: 31, tool_calls: 117, errors: 0, prompt_tokens: 18800, completion_tokens: 5200, cost: 42.8, runtime_sec: 188.1, ended_at: '2025-02-05T14:32:00Z', started_at: '2025-02-05T14:08:00Z', status: 1 },
  ],
  'member-growth-console': [
    { chat_id: 'run_growth_1', workflow_name: 'GrowthScoringWorkflow', user_id: 'nora@example.com', agent_turns: 94, tool_calls: 320, errors: 2, prompt_tokens: 64000, completion_tokens: 18000, cost: 182.5, runtime_sec: 421.6, ended_at: null, started_at: '2025-02-05T18:10:00Z', status: 0 },
    { chat_id: 'run_growth_2', workflow_name: 'CampaignReviewWorkflow', user_id: 'sean@example.com', agent_turns: 63, tool_calls: 245, errors: 1, prompt_tokens: 48100, completion_tokens: 12100, cost: 135.9, runtime_sec: 305.4, ended_at: '2025-02-05T15:11:00Z', started_at: '2025-02-05T14:42:00Z', status: 1 },
    { chat_id: 'run_growth_3', workflow_name: 'RetentionSignalsWorkflow', user_id: 'ariel@example.com', agent_turns: 28, tool_calls: 102, errors: 0, prompt_tokens: 15400, completion_tokens: 4200, cost: 38.2, runtime_sec: 141.5, ended_at: '2025-02-05T12:15:00Z', started_at: '2025-02-05T12:01:00Z', status: 1 },
  ],
  'campaign-revision-workbench': [
    { chat_id: 'run_campaign_1', workflow_name: 'RevisionWorkflow', user_id: 'owen@example.com', agent_turns: 8, tool_calls: 22, errors: 1, prompt_tokens: 3200, completion_tokens: 1200, cost: 8.4, runtime_sec: 51.2, ended_at: '2025-02-05T10:21:00Z', started_at: '2025-02-05T10:12:00Z', status: 1 },
  ],
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
      usage_prompt_tokens_final: run.prompt_tokens,
      usage_completion_tokens_final: run.completion_tokens,
      usage_total_cost_final: run.cost,
      tool_calls_final: run.tool_calls,
      errors_final: run.errors,
    })),
  ]),
)

const DEMO_BUILD_HISTORY_BY_APP = {
  'client-intake-copilot': [],
  'support-ops-assistant': [
    { id: 'artifact_support_1', version_number: 4, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-05T15:03:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/support-ops-assistant/4/app.zip' } } },
    { id: 'artifact_support_0', version_number: 3, lifecycle_status: 'draft', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-04T19:14:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/support-ops-assistant/3/app.zip' } } },
  ],
  'revenue-review-console': [
    { id: 'artifact_revenue_1', version_number: 2, lifecycle_status: 'draft', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-05T14:00:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/revenue-review-console/2/app.zip' } } },
  ],
  'partner-delivery-console': [
    { id: 'artifact_partner_2', version_number: 6, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-05T17:32:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/partner-delivery-console/6/app.zip' } } },
    { id: 'artifact_partner_1', version_number: 5, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-03T12:40:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/partner-delivery-console/5/app.zip' } } },
  ],
  'member-growth-console': [
    { id: 'artifact_growth_3', version_number: 11, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-05T18:05:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/member-growth-console/11/app.zip' } } },
    { id: 'artifact_growth_2', version_number: 10, lifecycle_status: 'current', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-02-02T13:22:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/member-growth-console/10/app.zip' } } },
    { id: 'artifact_growth_1', version_number: 9, lifecycle_status: 'draft', validation_status: 'passed', artifact_key: 'app_bundle', created_at: '2025-01-29T08:30:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/member-growth-console/9/app.zip' } } },
  ],
  'campaign-revision-workbench': [
    { id: 'artifact_campaign_1', version_number: 5, lifecycle_status: 'draft', validation_status: 'failed', artifact_key: 'app_bundle', created_at: '2025-02-05T11:18:00Z', commit_metadata: { metadata: { artifact_path: 'generated/apps/campaign-revision-workbench/5/app.zip' } } },
  ],
}

export function isConsoleDemoModeEnabled() {
  return Boolean(
    typeof import.meta !== 'undefined'
      && import.meta.env?.DEV
      && String(import.meta.env?.VITE_MOCK_MODE || '').toLowerCase() === 'true',
  )
}

export function buildConsoleDemoApps() {
  return DEMO_APPS.map((app) => ({ ...app }))
}

export function buildConsoleDemoAppSummary(appId) {
  const app = DEMO_APPS.find((entry) => entry.app_id === appId) || DEMO_APPS[0]
  const workflows = DEMO_WORKFLOWS_BY_APP[app?.app_id] || []
  return {
    app: {
      id: app?.app_id,
      app_id: app?.app_id,
      name: app?.name,
      description: app?.description,
      build_registry_id: app?.build_registry_id,
      lifecycle_state: app?.status || 'draft',
      lifecycle_label: String(app?.status || 'draft')
        .replaceAll('_', ' ')
        .replace(/\b\w/g, (match) => match.toUpperCase()),
      updated_at: app?.updated_at,
      created_at: app?.created_at,
      journey: app?.status === 'draft' ? 'greenfield_app' : 'refinement',
      first_goal: 'Operate the next lifecycle step from a clean app-scoped console.',
      host_owned_summary: 'Shared runtime, auth, and deployment substrate remain host-owned.',
    },
    ai: {
      provider: 'openai',
      model: 'gpt-4o-mini',
    },
    theme: {
      primary: '#28c7ff',
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
          ? 'Use the app console to review runtime health, revenue, integrations, and operations.'
          : app?.status === 'deploying'
            ? 'Complete deployment checks, then hand this app into live operation.'
            : 'Continue Build to move this app toward its next lifecycle gate.',
    },
  }
}

export function buildConsoleDemoRuntimeSummary() {
  return JSON.parse(JSON.stringify({
    ...DEMO_RUNTIME_SUMMARY,
    runtime_integrations: DEMO_RUNTIME_SUMMARY.integrations,
    app_connectors: [],
    connector_summary: {},
  }))
}

export function listConsoleDemoAppConnectors(appId) {
  return (DEMO_APP_CONNECTORS[appId] || []).map((connector) => ({ ...connector }))
}

export function getConsoleDemoUsageRecord(appId) {
  const record = DEMO_USAGE_BY_APP[appId]
  return record ? { ...record } : null
}

export function getConsoleDemoBillingRecord(appId) {
  const record = DEMO_BILLING_BY_APP[appId]
  return record ? { ...record } : null
}

export function getConsoleDemoDeploymentRecord(appId) {
  const record = DEMO_DEPLOYMENT_BY_APP[appId]
  return record ? { ...record, domains: [...(record.domains || [])] } : null
}

export function getConsoleDemoUsersRecord(appId) {
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

export function getConsoleDemoActivity(appId) {
  return (DEMO_ACTIVITY_BY_APP[appId] || []).map((entry) => ({ ...entry }))
}

export function getConsoleDemoWorkflowNames(appId) {
  return [...(DEMO_WORKFLOWS_BY_APP[appId] || [])]
}

export function getConsoleDemoRuns(appId) {
  return (DEMO_RUNS_BY_APP[appId] || []).map((run) => ({ ...run }))
}

export function getConsoleDemoSessions(appId) {
  return (DEMO_SESSIONS_BY_APP[appId] || []).map((session) => ({ ...session }))
}

export function getConsoleDemoBuildHistory(appId) {
  return {
    artifact_versions: (DEMO_BUILD_HISTORY_BY_APP[appId] || []).map((version) => ({
      ...version,
      commit_metadata: JSON.parse(JSON.stringify(version.commit_metadata || {})),
    })),
    change_requests: [],
  }
}

export function getConsoleDemoAdminStats(appId) {
  const usage = getConsoleDemoUsageRecord(appId)
  if (!usage) {
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
    active_chats: usage.workflow_runs > 0 ? 1 : 0,
    tracked_chats: usage.workflow_runs,
    total_agent_turns: Math.round((usage.workflow_runs || 0) * 1.8),
    total_tool_calls: usage.tool_calls,
    total_errors: usage.errors,
    total_prompt_tokens: Math.round((usage.tokens_used || 0) * 0.72),
    total_completion_tokens: Math.round((usage.tokens_used || 0) * 0.28),
    total_cost: usage.llm_cost_usd,
  }
}

export default buildConsoleDemoApps
