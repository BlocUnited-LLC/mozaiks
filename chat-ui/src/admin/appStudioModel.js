const APP_LIFECYCLE_META = {
  draft: {
    tone: 'default',
    label: 'Draft',
    primaryAction: 'build',
    primaryActionLabel: 'Continue Build',
  },
  building: {
    tone: 'primary',
    label: 'Building',
    primaryAction: 'build',
    primaryActionLabel: 'Continue Build',
  },
  review: {
    tone: 'primary',
    label: 'Review',
    primaryAction: 'build',
    primaryActionLabel: 'Continue Build',
  },
  configuring: {
    tone: 'primary',
    label: 'Configuring',
    primaryAction: 'build',
    primaryActionLabel: 'Continue Build',
  },
  deploying: {
    tone: 'primary',
    label: 'Deploying',
    primaryAction: 'overview',
    primaryActionLabel: 'Open App Studio',
  },
  active: {
    tone: 'success',
    label: 'Active',
    primaryAction: 'overview',
    primaryActionLabel: 'Open App Studio',
  },
  needs_revision: {
    tone: 'warning',
    label: 'Needs Revision',
    primaryAction: 'build',
    primaryActionLabel: 'Continue Build',
  },
  archived: {
    tone: 'default',
    label: 'Archived',
    primaryAction: 'overview',
    primaryActionLabel: 'Open App Studio',
  },
}

export const APP_STATUS_TONES = Object.fromEntries(
  Object.entries(APP_LIFECYCLE_META).map(([status, meta]) => [status, meta.tone]),
)

export const APP_STATUS_LABELS = Object.fromEntries(
  Object.entries(APP_LIFECYCLE_META).map(([status, meta]) => [status, meta.label]),
)

export const APP_BUILD_STATUSES = new Set(
  Object.entries(APP_LIFECYCLE_META)
    .filter(([, meta]) => meta.primaryAction === 'build')
    .map(([status]) => status),
)

export const APP_READY_STATUSES = new Set(['deploying', 'active', 'archived'])

const JOURNEY_LABELS = {
  brownfield_app: 'Existing App',
  greenfield_app: 'New App',
  refinement: 'Refinement',
}

const RUNTIME_READINESS_LABELS = {
  no_workflows: 'No workflows configured',
  workflows_present_no_entry_point: 'Workflow entry point still needs setup',
  entry_point_configured: 'Runtime entry point configured',
}

const PLAN_STATE_LABELS = {
  not_started: 'Not started',
  draft_saved: 'Draft saved',
  plan_ready: 'Review ready',
}

const APPROVAL_STATE_LABELS = {
  not_started: 'Not started',
  pending: 'Pending',
  approved: 'Approved',
  rejected: 'Rejected',
}

const GENERIC_APP_NAMES = new Set(['new app', 'untitled app', 'untitled'])
const DEFAULT_DRAFT_DESCRIPTION = 'Capture the first app brief, then keep the app moving from the Studio overview.'

export function normalizeAppStatus(status) {
  const normalized = String(status || '').trim()
  return APP_LIFECYCLE_META[normalized] ? normalized : 'draft'
}

export function formatAppShortId(appId) {
  if (!appId) return 'Draft'
  const compact = String(appId).trim().split('-').filter(Boolean).pop() || String(appId).trim()
  return compact.slice(0, 6).toUpperCase()
}

export function isGenericAppName(name) {
  if (!name) return true
  return GENERIC_APP_NAMES.has(String(name).trim().toLowerCase())
}

export function getAppDisplayName(app) {
  const status = normalizeAppStatus(app?.status || app?.lifecycle_state)
  const rawName = app?.name || app?.app_id || 'Untitled app'
  if (status === 'draft' && isGenericAppName(rawName)) {
    return `Draft ${formatAppShortId(app?.app_id || app?.id)}`
  }
  return rawName
}

export function getAppDisplayDescription(app) {
  const status = normalizeAppStatus(app?.status || app?.lifecycle_state)
  const description = String(app?.description || '').trim()
  if (status === 'draft' && (!description || description.toLowerCase() === DEFAULT_DRAFT_DESCRIPTION.toLowerCase())) {
    return 'Add the first build brief to define this app.'
  }
  return description
}

export function getAppLifecycleMeta(status, explicitLabel = null) {
  const normalized = normalizeAppStatus(status)
  const meta = APP_LIFECYCLE_META[normalized]
  return {
    status: normalized,
    tone: meta.tone,
    label: explicitLabel || meta.label,
    primaryAction: meta.primaryAction,
    primaryActionLabel: meta.primaryActionLabel,
  }
}

export function getAppStatusTone(status) {
  return getAppLifecycleMeta(status).tone
}

export function getAppLifecycleLabel(status, explicitLabel = null) {
  const normalized = String(status || '').trim()
  if (explicitLabel) return explicitLabel
  if (APP_STATUS_LABELS[normalized]) return APP_STATUS_LABELS[normalized]
  return normalized ? normalized.replaceAll('_', ' ') : 'Draft'
}

export function isAppInBuild(status) {
  return APP_BUILD_STATUSES.has(normalizeAppStatus(status))
}

export function isAppReady(status) {
  return APP_READY_STATUSES.has(normalizeAppStatus(status))
}

export function isAppPreparingDeploy(status) {
  return ['review', 'configuring', 'deploying'].includes(normalizeAppStatus(status))
}

export function isAppDeployReady(status) {
  return ['review', 'configuring', 'deploying', 'active'].includes(normalizeAppStatus(status))
}

export function getAppStudioDestination(app) {
  const appId = encodeURIComponent(app?.app_id || app?.id || '')
  if (!appId) return '/apps'
  return `/apps/${appId}/overview`
}

export function getAppPrimaryAction(app) {
  const lifecycle = getAppLifecycleMeta(app?.status || app?.lifecycle_state)
  return {
    kind: lifecycle.primaryAction,
    label: lifecycle.primaryActionLabel,
    href: getAppStudioDestination(app),
  }
}

export function getAppJourneyLabel(journey) {
  return JOURNEY_LABELS[String(journey || '').trim()] || 'App Studio'
}

export function getRuntimeReadinessLabel(value) {
  return RUNTIME_READINESS_LABELS[String(value || '').trim()] || 'Runtime readiness pending'
}

export function getPlanStateLabel(value) {
  return PLAN_STATE_LABELS[String(value || '').trim()] || 'Draft saved'
}

export function getApprovalStateLabel(value) {
  return APPROVAL_STATE_LABELS[String(value || '').trim()] || 'Not started'
}

export function getDeploymentHealthLabel(status) {
  const normalized = normalizeAppStatus(status)
  if (normalized === 'active') return 'Live'
  if (normalized === 'deploying') return 'Deploying'
  if (normalized === 'review' || normalized === 'configuring') return 'Preparing release'
  if (normalized === 'archived') return 'Archived'
  return 'Pre-deploy'
}

export function getHostingStateLabel(status) {
  const normalized = normalizeAppStatus(status)
  if (normalized === 'active') return 'Managed'
  if (normalized === 'deploying') return 'Provisioning'
  if (normalized === 'archived') return 'Archived'
  if (normalized === 'review' || normalized === 'configuring') return 'Pending setup'
  return 'Not assigned'
}

export function getRuntimeHealthLabel(status, totalErrors = null) {
  const normalized = normalizeAppStatus(status)
  if (normalized === 'archived') return 'Archived'
  if (normalized !== 'active') return 'Not live yet'
  if (typeof totalErrors === 'number' && totalErrors > 0) return 'Needs attention'
  return 'Healthy'
}

export function getLifecycleGuidance(status) {
  const normalized = normalizeAppStatus(status)
  if (normalized === 'draft') {
    return 'Capture the first app brief so the draft becomes a tracked workstream with saved progress.'
  }
  if (normalized === 'building') {
    return 'Use Overview, Integrations, Billing, and Hosting to review the latest work and move the app toward production.'
  }
  if (normalized === 'review') {
    return 'Review the latest output, align integrations and billing, and finish the remaining production checks.'
  }
  if (normalized === 'configuring') {
    return 'Complete integrations, billing setup, and hosting readiness before launch.'
  }
  if (normalized === 'deploying') {
    return 'Monitor hosting progress and confirm production readiness before broader rollout.'
  }
  if (normalized === 'needs_revision') {
    return 'The app needs a scoped revision. Keep the current state visible here until the next approved change is ready.'
  }
  if (normalized === 'archived') {
    return 'This app is archived. Keep overview, usage, billing, and hosting history available for reference.'
  }
  return 'This app is live. Use Users, Usage, Billing, Hosting, and Integrations to manage the running experience.'
}

export function getAppSectionMessage(status, section) {
  const normalized = normalizeAppStatus(status)
  const area = String(section || '').trim()

  if (area === 'usage') {
    return isAppInBuild(normalized)
      ? 'Usage reporting will expand as this app moves closer to production.'
      : normalized === 'archived'
        ? 'Archived apps keep their latest usage context available in App Studio.'
        : 'Open App Studio for detailed token, cost, and runtime usage.'
  }

  if (area === 'operations') {
    return isAppInBuild(normalized)
      ? 'Use Overview and Hosting to resolve pending readiness, rollout, or configuration work.'
      : normalized === 'archived'
        ? 'Open App Studio to review archived operational history and prior incidents.'
        : 'Open Hosting or Usage from App Studio for runtime posture and production detail.'
  }

  if (area === 'billing') {
    return isAppInBuild(normalized)
      ? 'Billing signals become more useful as the app moves through review and hosting readiness.'
      : normalized === 'archived'
        ? 'Archived apps remain visible here so billing history stays traceable.'
        : 'Use Billing and Hosting together to manage commercial readiness and production posture.'
  }

  if (area === 'settings') {
    return isAppInBuild(normalized)
      ? 'Use Integrations, Billing, and Hosting to keep the current production setup visible as work progresses.'
      : 'Open App Studio to review the production-facing controls that are already live for this app.'
  }

  return getLifecycleGuidance(normalized)
}

export function formatStudioDate(
  value,
  fallback = 'Not available',
  options = { month: 'short', day: 'numeric', year: 'numeric' },
) {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleDateString(undefined, options)
}

export function formatStudioDateTime(value, fallback = 'Not available') {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fallback
  return parsed.toLocaleString()
}

export function getAppRecordSnapshot(app) {
  const lifecycle = getAppLifecycleMeta(app?.status || app?.lifecycle_state, app?.lifecycle_label || null)
  return {
    status: lifecycle.status,
    lifecycleLabel: lifecycle.label,
    lifecycleTone: lifecycle.tone,
    deploymentHealth: getDeploymentHealthLabel(lifecycle.status),
    hostingState: getHostingStateLabel(lifecycle.status),
    updatedLabel: formatStudioDateTime(app?.updated_at || app?.created_at, 'Just created'),
    guidance: getLifecycleGuidance(lifecycle.status),
    primaryAction: getAppPrimaryAction(app),
  }
}
