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

// `error_code` values a check reports when it could not ask the provider at
// all. They say nothing about the saved values.
const INCONCLUSIVE_ERROR_CODES = new Set(['timeout', 'connection_error', 'unexpected_status'])

/**
 * What a health record says. Accepts a connector's `health` or a
 * `check_workspace_connector_health` response; both carry `status`,
 * `message`, and `error_code`.
 *
 * - `passed`: the provider accepted the saved values.
 * - `failed`: the saved values cannot work. The provider refused them
 *   (`status: unhealthy`), or the check found them missing or malformed and
 *   said so in `error_code` (`missing_credentials`, `invalid_key_format`, ...)
 *   while leaving `status` at `unknown` or `pending_validation`.
 * - `inconclusive`: the provider could not be asked (timeout, connection error).
 * - `none`: no check has reported anything.
 */
export function checkOutcome(health) {
  if (!health || typeof health !== 'object') return 'none'
  if (health.status === 'healthy') return 'passed'
  if (health.status === 'unhealthy') return 'failed'
  const code = String(health.error_code || '').trim()
  if (!code) return 'none'
  return INCONCLUSIVE_ERROR_CODES.has(code) ? 'inconclusive' : 'failed'
}

/** The connector's last health check found that it cannot work as saved. */
export function hasFailedCheck(connector) {
  return Boolean(connector) && checkOutcome(connector.health) === 'failed'
}

/** The provider itself refused the saved credential. */
export function isRejected(connector) {
  return hasFailedCheck(connector) && connector.health?.error_code === 'auth_failed'
}

/**
 * Whether one connector can be used, for pages that only need that:
 * - `missing`: no credential is saved;
 * - `expired`: the saved credential has expired;
 * - `check_failed`: the last check found it cannot work as saved;
 * - `incomplete`: a credential is saved but required setup is missing;
 * - `ok`: none of the above is known.
 */
export function connectorCredentialState(connector) {
  if (!connector?.secret_available) return 'missing'
  if (isExpired(connector)) return 'expired'
  if (hasFailedCheck(connector)) return 'check_failed'
  if (connector.ready === false) return 'incomplete'
  return 'ok'
}

/** Merge a saved connector's state into its catalog row. */
export function withConnectorOverlay(item, connector) {
  if (!connector) return item
  const lifecycle = connectorLifecycle(connector)
  const overlay = {
    connector_lifecycle: lifecycle,
    connector_days_until_expiry: connector.days_until_expiry ?? null,
  }
  // A failed check outranks everything except expiry: whatever else is true
  // of the connector, it does not work.
  if (hasFailedCheck(connector) && lifecycle !== 'expired') {
    return { ...item, ...overlay, display_status: 'unhealthy', workspace_connector_status: 'check_failed' }
  }
  if (connector.ready) {
    const expiring = lifecycle === 'expiring'
    return {
      ...item,
      ...overlay,
      display_status: expiring ? 'expiring' : 'configured',
      workspace_connector_status: expiring ? 'expiring' : 'ready',
    }
  }
  return { ...item, ...overlay, workspace_connector_status: lifecycle === 'expired' ? 'expired' : 'partial' }
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

function providerName(displayName) {
  return String(displayName || '').trim() || 'the provider'
}

/**
 * Why a connector's last check failed, from its stored health record, or ''.
 *
 * The stored record keeps the provider's own sentence only when its status is
 * `unhealthy`. When the failure was reported through `error_code`, the stored
 * message is a generic status line and says nothing about the cause.
 */
export function failedCheckReason(connector) {
  const health = connector?.health
  if (!health || health.status !== 'unhealthy') return ''
  return String(health.message || '').trim()
}

/**
 * What to tell the owner about a check the page just ran. Returns null when
 * the check passed or reported nothing.
 *
 * @param {object|null} health  the `check_workspace_connector_health` response
 * @param {string} displayName  the service's name, for the sentence
 * @param {{ afterSave?: boolean }} [options]  true when the check followed a save
 */
export function checkProblemMessage(health, displayName, { afterSave = true } = {}) {
  const outcome = checkOutcome(health)
  if (outcome !== 'failed' && outcome !== 'inconclusive') return null
  const reason = String(health.message || '').trim()
  const provider = providerName(displayName)
  const happened = outcome === 'failed' ? `the check with ${provider} failed` : `${provider} could not be checked`
  const lead = afterSave ? `The key was saved, but ${happened}` : `${happened[0].toUpperCase()}${happened.slice(1)}`
  const sentence = reason ? `${lead}: ${reason}` : `${lead}.`
  return outcome === 'failed' ? sentence : `${sentence.replace(/\.?$/, '.')} Use Test connection to try again.`
}

/**
 * Save a key, ask the provider about it, then reload, in that order.
 *
 * The check is awaited before the reload so the reloaded list carries its
 * result; when the check was fire-and-forget the list showed "Connected" for
 * a key the provider had just refused.
 *
 * @param {object} steps
 * @param {() => Promise<object>} steps.save    stores the key; rejects when the server refuses it
 * @param {() => Promise<object>} steps.check   runs the provider health check
 * @param {() => Promise<void>} steps.reload    reloads the page's lists
 * @param {string} steps.displayName
 * @returns {Promise<{ problem: string|null }>} `problem` is what to show the owner, or null when the key works
 */
export async function saveAndCheckConnector({ save, check, reload, displayName }) {
  let saveResult
  try {
    saveResult = await save()
  } catch (error) {
    // A refused save can still have rewritten the stored record.
    await Promise.resolve(reload()).catch(() => {})
    throw error
  }
  let problem = null
  if (saveResult?.health_check_supported) {
    try {
      problem = checkProblemMessage(await check(), displayName)
    } catch {
      problem = `The key was saved, but the check with ${providerName(displayName)} could not run. Use Test connection to try again.`
    }
  }
  await reload()
  return { problem }
}
