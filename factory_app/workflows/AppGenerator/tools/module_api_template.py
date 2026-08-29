"""
module_api_template — canonical template for ui/lib/moduleApi.js.

Generated apps that use custom_route_bundle need a module action helper.
This module provides the canonical implementation that is injected into
the generated bundle by generate_and_download.py when the agent did not
already produce one.

Behavioral contract:
  - All module action calls use POST /api/modules/{module}/{action}.
  - Auth token is read from window.mozaiksAuth or sessionStorage fallback keys.
  - Successful responses return parsed JSON.
  - Non-ok responses throw an Error with structured fields preserved:
      err.error_code  — backend error code (e.g. "RECORD_NOT_FOUND")
      err.code        — alias when backend uses .code instead of .error_code
      err.status      — HTTP status code (integer)
      err.data        — full parsed body for caller inspection
      isInsufficientTokensError(err) detects runtime token depletion.
      insufficientTokensRecoveryPath(err) returns the app-local recovery route.
      isEntitlementRequiredError(err) detects plan entitlement denial.
      entitlementUpgradePath(err) returns the app-local upgrade/pricing route.
    This lets generated custom-route JSX branch on backend states:
      try { ... } catch (err) {
        if (err.error_code === 'RECORD_NOT_FOUND') { ... }
      }
  - Non-JSON error bodies (HTML gateway errors, etc.) are handled gracefully.
  - No secrets or provider credentials are attached to thrown errors.

Called by generate_and_download.py:

    from factory_app.workflows.AppGenerator.tools.module_api_template import (
        get_module_api_template,
    )
    if "ui/lib/moduleApi.js" not in files_map:
        files_map["ui/lib/moduleApi.js"] = get_module_api_template()
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical template
# ---------------------------------------------------------------------------

_MODULE_API_TEMPLATE = """\
/**
 * ui/lib/moduleApi.js — generated module action helper.
 *
 * Provides moduleAction() for calling app module actions from custom-route JSX.
 * All requests use POST /api/modules/{module}/{action} with JSON bodies.
 *
 * Error handling:
 *   Non-ok responses throw an Error with structured fields attached:
 *     err.error_code  — backend error code string (e.g. "RECORD_NOT_FOUND")
 *     err.code        — alternate error code field
 *     err.status      — HTTP status integer
 *     err.data        — full parsed response body
 *   Catch and branch on err.error_code to handle specific backend states.
 *   For INSUFFICIENT_TOKENS, navigate to insufficientTokensRecoveryPath(err)
 *   and do not retry the action automatically.
 *   For ENTITLEMENT_REQUIRED, navigate to entitlementUpgradePath(err)
 *   and do not retry the action automatically.
 *
 *   Example:
 *     try {
 *       const result = await moduleAction('inventory', 'get_item', { item_id: id })
 *       setItem(result.item)
 *     } catch (err) {
 *       if (err.error_code === 'ITEM_NOT_FOUND') setNotFound(true)
 *       else setError(err.message)
 *     }
 */

export const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
  'http://localhost:8000'

export function getAccessToken() {
  if (typeof window !== 'undefined' && window.mozaiksAuth?.getAccessToken) {
    return window.mozaiksAuth.getAccessToken()
  }
  if (typeof sessionStorage === 'undefined') return null
  const appPrefix =
    (typeof import.meta !== 'undefined' && (
      import.meta.env?.VITE_APP_SLUG ||
      import.meta.env?.VITE_APP_ID
    )) ||
    ''
  if (appPrefix) {
    const appToken = sessionStorage.getItem(`${appPrefix}_access_token`)
    if (appToken) return appToken
  }
  for (let i = 0; i < sessionStorage.length; i += 1) {
    const key = sessionStorage.key(i)
    if (key?.endsWith('_access_token')) {
      const token = sessionStorage.getItem(key)
      if (token) return token
    }
  }
  return (
    sessionStorage.getItem('mozaiks_access_token') ||
    sessionStorage.getItem('chatui_token') ||
    sessionStorage.getItem('access_token')
  )
}

export function authHeaders() {
  const token = getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function parseErrorPayload(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return null
  const detail = body.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) return detail
  return body
}

function tokenRecoveryMetadata(err) {
  const data = err?.data || {}
  return data.extra_data || data.metadata || data
}

export function isInsufficientTokensError(err) {
  return (
    err?.error_code === 'INSUFFICIENT_TOKENS' ||
    err?.code === 'INSUFFICIENT_TOKENS' ||
    err?.data?.error_code === 'INSUFFICIENT_TOKENS' ||
    err?.data?.extra_data?.error_code === 'INSUFFICIENT_TOKENS'
  )
}

export function insufficientTokensRecoveryPath(err, fallback = '/billing') {
  const metadata = tokenRecoveryMetadata(err)
  return (
    metadata.top_up_route ||
    metadata.billing_route ||
    metadata.upgrade_route ||
    metadata.contact_route ||
    fallback
  )
}

export function isEntitlementRequiredError(err) {
  // HTTP 402 is the canonical signal — the backend maps ENTITLEMENT_REQUIRED to
  // it in one place. Checking status as well as error_code means a denial is
  // still recognised if the body is unreadable or reshaped in transit.
  return (
    err?.status === 402 ||
    err?.error_code === 'ENTITLEMENT_REQUIRED' ||
    err?.code === 'ENTITLEMENT_REQUIRED' ||
    err?.data?.error_code === 'ENTITLEMENT_REQUIRED' ||
    err?.data?.detail?.error_code === 'ENTITLEMENT_REQUIRED'
  )
}

export function entitlementUpgradePath(err, fallback = '/pricing') {
  const metadata = tokenRecoveryMetadata(err)
  return (
    metadata.upgrade_route ||
    metadata.billing_route ||
    metadata.pricing_route ||
    fallback
  )
}

export async function moduleAction(moduleName, actionName, input = {}) {
  const response = await fetch(`${API_BASE}/api/modules/${moduleName}/${actionName}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(input || {}),
  })

  if (!response.ok) {
    // Parse the JSON body when available so callers can inspect structured
    // backend error fields such as error_code, code, and action-specific data.
    let body = null
    try { body = await response.json() } catch { /* non-JSON body — ignore */ }
    // FastAPI serializes HTTPException(detail={...}) as {"detail": {...}}, so
    // the structured fields live one level down. Fall back to the raw body for
    // responses that are already flat (proxies, non-FastAPI intermediaries).
    const payload = parseErrorPayload(body)
    const err = new Error(
      payload?.error || payload?.message ||
      `Module action failed: ${moduleName}.${actionName} ${response.status}`
    )
    // Attach structured fields so catch blocks can branch on backend states.
    if (payload?.error_code) err.error_code = payload.error_code
    if (payload?.code)       err.code       = payload.code
    err.status = response.status
    // Preserve the unwrapped payload for callers that need additional context
    // fields. Do not attach secrets or provider credentials here.
    if (payload != null) err.data = payload
    throw err
  }

  return response.json()
}

export async function startWorkflow(workflowName, contextVariables = {}) {
  const appId =
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_APP_ID) ||
    'app'
  const response = await fetch(
    `${API_BASE}/api/chats/${encodeURIComponent(appId)}/${encodeURIComponent(workflowName)}/start`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ context_variables: contextVariables }),
    }
  )
  if (!response.ok) {
    let body = null
    try { body = await response.json() } catch { /* non-JSON */ }
    // Use the same strict envelope parser as moduleAction so workflow and
    // module errors cannot drift or accept arrays/primitive JSON as payloads.
    const payload = parseErrorPayload(body)
    const err = new Error(
      payload?.error || payload?.message ||
      `Failed to start ${workflowName}: ${response.status}`
    )
    if (payload?.error_code) err.error_code = payload.error_code
    err.status = response.status
    if (payload != null) err.data = payload
    throw err
  }
  return response.json()
}

export function moduleWebSocketUrl(path, params = {}) {
  const base = API_BASE.startsWith('https')
    ? API_BASE.replace(/^https/, 'wss')
    : API_BASE.replace(/^http/, 'ws')
  const url = new URL(`${base.replace(/\\/+$/, '')}${path}`)
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, value)
    }
  })
  return url.toString()
}
"""


def get_module_api_template() -> str:
    """Return the canonical ui/lib/moduleApi.js template content."""
    return _MODULE_API_TEMPLATE

