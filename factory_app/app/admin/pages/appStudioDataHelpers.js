import {
  getStudioDemoDeploymentRecord,
  getStudioDemoBillingRecord,
  getStudioDemoUsageRecord,
  getStudioDemoUsersRecord,
} from './studioDemoData.js'

export function toArray(value) {
  return Array.isArray(value) ? value : []
}

export function sumBy(items, selector) {
  return toArray(items).reduce((total, item) => total + Number(selector(item) || 0), 0)
}

export function groupBy(items, getKey, buildInitialValue, mergeValue) {
  const groups = new Map()

  for (const item of toArray(items)) {
    const key = getKey(item)
    if (!key) continue
    if (!groups.has(key)) {
      groups.set(key, buildInitialValue(item))
      continue
    }
    groups.set(key, mergeValue(groups.get(key), item))
  }

  return Array.from(groups.values())
}

export function getAppStudioSnapshot(appId, data, dataMode = 'live') {
  const summary = data?.summary || {}
  const app = summary.app || {}
  const stats = data?.stats || {}
  const runs = toArray(data?.runs?.runs)
  const sessions = toArray(data?.sessions?.sessions)
  const buildHistory = toArray(data?.buildHistory?.artifact_versions)
  const workflowNames = toArray(summary?.workspace?.workflow_names)
  const integrations = data?.integrations || {}
  const appConnectors = toArray(integrations.app_connectors)
  const runtimeIntegrations = integrations.runtime_integrations || integrations.integrations || {}
  const connectorSummary = integrations.connector_summary || {}
  const demoEnabled = dataMode === 'demo'

  return {
    summary,
    app,
    stats,
    runs,
    sessions,
    buildHistory,
    workflowNames,
    integrations,
    appConnectors,
    runtimeIntegrations,
    connectorSummary,
    lifecycleState: app.lifecycle_state || 'draft',
    usersRecord: demoEnabled ? getStudioDemoUsersRecord(appId) : null,
    usageRecord: demoEnabled ? getStudioDemoUsageRecord(appId) : null,
    billingRecord: demoEnabled ? getStudioDemoBillingRecord(appId) : null,
    deploymentRecord: demoEnabled ? getStudioDemoDeploymentRecord(appId) : null,
  }
}

export function buildSubscriptionMix(usersRecord) {
  const counts = new Map()
  for (const user of toArray(usersRecord?.users)) {
    const subscription = String(user.subscription || 'Unassigned').trim() || 'Unassigned'
    counts.set(subscription, (counts.get(subscription) || 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([label, count]) => ({ label, count }))
    .sort((left, right) => right.count - left.count)
}
