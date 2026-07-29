import {
  getAppDisplayDescription,
  getAppDisplayName,
  getAppPrimaryAction,
  getAppRecordSnapshot,
  isAppInBuild,
  normalizeAppStatus,
} from './appStudioModel.js'
import { buildAppDashboardHref } from './dashboardRoutes.js'

function parseTimestamp(value) {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? 0 : parsed
}

function formatPortfolioUpdatedLabel(value) {
  const timestamp = parseTimestamp(value)
  if (!timestamp) return 'Recently'

  const today = new Date()
  const deltaMs = Math.max(0, today.getTime() - timestamp)
  const deltaMinutes = Math.floor(deltaMs / 60000)
  const deltaHours = Math.floor(deltaMs / 3600000)
  const targetDate = new Date(timestamp)
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
  const startOfTarget = new Date(
    targetDate.getFullYear(),
    targetDate.getMonth(),
    targetDate.getDate(),
  ).getTime()
  const dayDelta = Math.round((startOfToday - startOfTarget) / 86400000)

  if (dayDelta === 0) {
    if (deltaMinutes < 1) return 'Just now'
    if (deltaMinutes < 60) return `${deltaMinutes} min ago`
    return `${Math.max(deltaHours, 1)}h ago`
  }
  if (dayDelta === 1) return 'Yesterday'

  return targetDate.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function getUsageState(status) {
  if (status === 'active') {
    return {
      id: 'live',
      label: 'Live metering',
      tone: 'success',
    }
  }
  if (['review', 'configuring', 'deploying'].includes(status)) {
    return {
      id: 'staging',
      label: 'Staging readiness',
      tone: 'primary',
    }
  }
  if (status === 'archived') {
    return {
      id: 'historical',
      label: 'Historical only',
      tone: 'default',
    }
  }
  return {
    id: 'build',
    label: 'Build only',
    tone: status === 'needs_revision' ? 'warning' : 'default',
  }
}

function getOperationsState(status) {
  if (status === 'needs_revision') {
    return {
      label: 'Blocked',
      tone: 'destructive',
      severity: 0,
    }
  }
  if (['review', 'configuring'].includes(status)) {
    return {
      label: 'Release blocked',
      tone: 'warning',
      severity: 1,
    }
  }
  if (['draft', 'building'].includes(status)) {
    return {
      label: 'In build',
      tone: 'primary',
      severity: 2,
    }
  }
  if (status === 'deploying') {
    return {
      label: 'Provisioning',
      tone: 'primary',
      severity: 3,
    }
  }
  if (status === 'archived') {
    return {
      label: 'Archived',
      tone: 'default',
      severity: 5,
    }
  }
  return {
    label: 'Healthy',
    tone: 'success',
    severity: 4,
  }
}

function getPortfolioSortPriority(status, operationsState) {
  if (operationsState.severity <= 1) return operationsState.severity
  if (status === 'building') return 2
  if (status === 'deploying') return 3
  if (status === 'active') return 4
  if (status === 'draft') return 5
  if (status === 'archived') return 6
  return operationsState.severity
}

function getPortfolioStateLabel(status, snapshot) {
  if (status === 'active') {
    return `${snapshot.hostingState} · ${snapshot.deploymentHealth}`
  }
  if (status === 'deploying') {
    return `${snapshot.hostingState} · ${snapshot.deploymentHealth}`
  }
  if (status === 'review' || status === 'configuring') {
    return `Pre-deploy · ${snapshot.hostingState}`
  }
  if (status === 'needs_revision') {
    return 'Pre-deploy · Needs revision'
  }
  if (status === 'archived') {
    return 'Archived · Not live'
  }
  if (status === 'building') {
    return 'Build · Not deployed'
  }
  return 'Draft · Not deployed'
}

function getFilterBucket(status, operationsState) {
  if (operationsState.severity <= 1) return 'needs-input'
  if (status === 'active') return 'live'
  if (isAppInBuild(status)) return 'building'
  return 'other'
}

function buildRow(app, options = {}) {
  const status = normalizeAppStatus(app?.status || app?.lifecycle_state)
  const snapshot = getAppRecordSnapshot(app)
  const operationsState = getOperationsState(status)
  const updatedAt = parseTimestamp(app?.updated_at || app?.created_at)
  const stateLabel = getPortfolioStateLabel(status, snapshot)
  const updatedLabel = formatPortfolioUpdatedLabel(app?.updated_at || app?.created_at)
  const name = getAppDisplayName(app)
  const description = getAppDisplayDescription(app) || snapshot.guidance

  const appId = app?.app_id || app?.id || null
  const dashboardHref = appId && isAppInBuild(status)
    ? buildAppDashboardHref(options.appDashboardRoute, appId)
    : null

  return {
    app,
    id: app?.build_registry_id || app?.app_id || app?.name || `${status}-pending`,
    name,
    description,
    status,
    snapshot,
    updatedAt,
    updatedLabel,
    stateLabel,
    usageState: getUsageState(status),
    operationsState,
    primaryAction: getAppPrimaryAction(app),
    dashboardHref,
    sortPriority: getPortfolioSortPriority(status, operationsState),
    filterBucket: getFilterBucket(status, operationsState),
    searchText: [name, description, snapshot.lifecycleLabel, stateLabel].join(' ').toLowerCase(),
  }
}

function sortRowsByUpdatedAt(rows) {
  return [...rows].sort((left, right) => right.updatedAt - left.updatedAt || left.name.localeCompare(right.name))
}

export function buildWorkspacePortfolio(apps, options = {}) {
  const rows = sortRowsByUpdatedAt(
    (Array.isArray(apps) ? apps : []).map((app) => buildRow(app, options)),
  )
  const totalApps = rows.length
  const activeCount = rows.filter((row) => row.status === 'active').length
  const buildCount = rows.filter((row) => isAppInBuild(row.status)).length
  const blockingAlerts = rows.filter((row) => row.operationsState.severity <= 1).length
  const latestUpdatedLabel = rows[0]?.updatedLabel || 'No app activity yet'

  return {
    rows,
    totalApps,
    activeCount,
    buildCount,
    blockingAlerts,
    latestUpdatedLabel,
  }
}

export default buildWorkspacePortfolio
