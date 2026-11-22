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
 * What a health record says. Accepts a connector's `health` or a
 * `check_workspace_connector_health` response; both carry `status`,
 * `message`, and `error_code`.
 *
 * - `passed`: the provider accepted the saved values.
 * - `failed`: the saved values cannot work. Either the check said so with
 *   `status: unhealthy` (a refused key, a malformed value, an unreachable
 *   database), or it found required values missing. That second case is
 *   reported as `error_code: missing_credentials` or `missing_api_base` with
 *   the status left at `unknown` or `pending_validation`, and the server
 *   still calls the connector ready.
 * - `inconclusive`: the check ran into something that says nothing about the
 *   saved values: a timeout, a connection error, an unexpected HTTP status, a
 *   secret that could not be read just now, a provider-specific error.
 * - `none`: no check has reported anything.
 *
 * Only `missing_*` codes count as a failure on their own. Any other code may
 * be transient, and calling it a failure would flag a working connection.
 */
export function checkOutcome(health) {
  if (!health || typeof health !== 'object') return 'none'
  if (health.status === 'healthy') return 'passed'
  if (health.status === 'unhealthy') return 'failed'
  const code = String(health.error_code || '').trim()
  if (!code) return 'none'
  return code.startsWith('missing_') ? 'failed' : 'inconclusive'
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

/**
 * What the app pages show for one connector: `{ state, pill, tone, detail }`.
 * The app Health page and the app Integrations page both use this, so the two
 * never give different verdicts for the same connector.
 */
export function connectorVerdict(connector) {
  const state = connectorCredentialState(connector)
  if (state === 'missing') return { state, pill: 'Needs secret', tone: 'warning', detail: 'Credential still needs to be saved.' }
  if (state === 'expired') return { state, pill: 'Expired', tone: 'destructive', detail: 'The saved credential has expired.' }
  if (state === 'check_failed') {
    return {
      state,
      pill: 'Check failed',
      tone: 'destructive',
      detail: failedCheckReason(connector) || 'The last check found this connection cannot be used as saved.',
    }
  }
  if (state === 'incomplete') return { state, pill: 'Needs setup', tone: 'warning', detail: 'A credential is saved, but setup is not complete.' }
  return { state, pill: 'Ready', tone: 'success', detail: 'Credential is configured.' }
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

/**
 * How to show a connector's last check in its panel: `{ label, tone, message }`.
 *
 * When the check found required values missing, the stored status is still
 * `pending_validation` and its message is a generic line. Showing those beside
 * "Check failed" contradicts it, so that case gets its own wording.
 *
 * @param {object|null} connector
 * @param {{ statusLabel?: (status: string) => string }} [options]  how the page words a raw status
 */
export function lastCheckDisplay(connector, { statusLabel: wordStatus = (status) => String(status) } = {}) {
  const health = connector?.health || {}
  if (hasFailedCheck(connector) && health.status !== 'unhealthy') {
    return {
      label: 'Failed',
      tone: 'destructive',
      message: 'The check found required values missing. Use Test connection to see which.',
    }
  }
  return { label: wordStatus(health.status), tone: statusTone(health.status), message: health.message || null }
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
 * @param {{ afterSave?: boolean, retryAction?: string }} [options]  `afterSave`: the check
 *   followed a save; `retryAction`: the name of the button that runs a check on this page
 */
export function checkProblemMessage(health, displayName, { afterSave = true, retryAction = 'Test connection' } = {}) {
  const outcome = checkOutcome(health)
  if (outcome !== 'failed' && outcome !== 'inconclusive') return null
  const reason = String(health.message || '').trim()
  const provider = providerName(displayName)
  const happened = outcome === 'failed' ? `the check with ${provider} failed` : `${provider} could not be checked`
  const lead = afterSave ? `The key was saved, but ${happened}` : `${happened[0].toUpperCase()}${happened.slice(1)}`
  const sentence = reason ? `${lead}: ${reason}` : `${lead}.`
  return outcome === 'failed' ? sentence : `${sentence.replace(/\.?$/, '.')} Use ${retryAction} to try again.`
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
