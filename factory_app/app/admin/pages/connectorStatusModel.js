/**
 * How Studio classifies a service and its saved connector.
 *
 * Pure functions over two server shapes: a catalog row from
 * `workspace_integrations.list_integrations` (`item`) and a saved connector
 * from `list_workspace_connectors` or `/api/studio/integrations` (`connector`).
 *
 * A connector is `ready` when its required values are stored. That says
 * nothing about whether the connection works: the answer to that is
 * `connector.health`, written by the health check. A connector whose last
 * check failed is not a working connection, so it is never shown as connected.
 */

export function statusTone(status) {
  if (status === 'configured' || status === 'healthy') return 'success'
  if (status === 'partial' || status === 'pending_validation' || status === 'expiring') return 'warning'
  if (status === 'missing' || status === 'unhealthy' || status === 'not_configured' || status === 'expired') return 'destructive'
  return 'default'
}

export function connectorLifecycle(connector) {
  return connector?.lifecycle_status || (connector?.ready ? 'active' : null)
}

export function isExpired(connector) {
  return connectorLifecycle(connector) === 'expired'
}

export function isExpiring(connector) {
  return connectorLifecycle(connector) === 'expiring'
}

/**
 * The connector's last health check failed. The provider may have refused the
 * credential (`error_code: auth_failed`) or the check may not have reached it
 * (`connection_error`, `timeout`); `health.message` says which.
 */
export function hasFailedCheck(connector) {
  return Boolean(connector) && connector.health?.status === 'unhealthy'
}

/** The provider itself refused the saved credential. */
export function isRejected(connector) {
  return hasFailedCheck(connector) && connector.health?.error_code === 'auth_failed'
}

/**
 * Whether a connector can be used, for pages that only need that:
 * `missing` (nothing saved), `check_failed` (saved, last check failed), or `ok`.
 */
export function connectorCredentialState(connector) {
  if (!connector?.secret_available) return 'missing'
  return hasFailedCheck(connector) ? 'check_failed' : 'ok'
}

/** Merge a saved connector's state into its catalog row. */
export function withConnectorOverlay(item, connector) {
  const lifecycle = connectorLifecycle(connector)
  if (connector?.ready) {
    let displayStatus = 'configured'
    if (hasFailedCheck(connector)) displayStatus = 'unhealthy'
    else if (lifecycle === 'expiring') displayStatus = 'expiring'
    const connectorStatus = { configured: 'ready', unhealthy: 'check_failed', expiring: 'expiring' }[displayStatus]
    return {
      ...item,
      display_status: displayStatus,
      workspace_connector_status: connectorStatus,
      connector_lifecycle: lifecycle,
      connector_days_until_expiry: connector?.days_until_expiry ?? null,
    }
  }
  if (connector) {
    return {
      ...item,
      workspace_connector_status: lifecycle === 'expired' ? 'expired' : 'partial',
      connector_lifecycle: lifecycle,
      connector_days_until_expiry: connector?.days_until_expiry ?? null,
    }
  }
  return item
}

export function effectiveStatus(item) {
  return item?.display_status || item?.status
}

/**
 * Whether the owner has something to do for this service. `connector` is
 * optional: without it, the fields `withConnectorOverlay` merged in are used.
 */
export function needsAttention(item, connector) {
  const usedByApps = Number(item?.app_usage_count || 0) > 0
  if (isExpired(connector) || isExpiring(connector) || hasFailedCheck(connector)) return true
  const lifecycle = item?.connector_lifecycle
  if (lifecycle === 'expired' || lifecycle === 'expiring') return true
  const status = effectiveStatus(item)
  return status === 'unhealthy' || status === 'partial' || (status === 'missing' && usedByApps)
}

/** The list a service appears under: `attention`, `connected`, or `available`. */
export function integrationGroup(item) {
  if (needsAttention(item)) return 'attention'
  return effectiveStatus(item) === 'configured' ? 'connected' : 'available'
}

export function statusLabel(status, item = null) {
  const resolvedStatus = item?.display_status || status
  if (resolvedStatus === 'configured') return 'Connected'
  if (resolvedStatus === 'unhealthy') return 'Check failed'
  if (resolvedStatus === 'expiring') return 'Expiring soon'
  if (resolvedStatus === 'partial') return 'Needs setup'
  if (resolvedStatus === 'missing' && Number(item?.app_usage_count || 0) > 0) return 'Needs setup'
  return 'Available'
}

export function displayTone(item) {
  const resolvedStatus = item?.display_status || item?.status
  if (resolvedStatus === 'missing' && Number(item?.app_usage_count || 0) === 0) return 'default'
  return statusTone(resolvedStatus)
}

/**
 * What to tell the owner after a key is saved and then checked with the
 * provider. Returns null when the check passed or did not run.
 *
 * @param {object|null} health  the `check_workspace_connector_health` response
 * @param {string} displayName  the service's name, for the sentence
 */
export function failedCheckMessage(health, displayName) {
  if (!health || health.status !== 'unhealthy') return null
  const provider = String(displayName || '').trim() || 'the provider'
  const reason = String(health.message || '').trim()
  return reason
    ? `The key was saved, but the check with ${provider} failed: ${reason}`
    : `The key was saved, but the check with ${provider} failed.`
}
