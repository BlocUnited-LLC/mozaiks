/**
 * ui/lib/moduleApi.js — fixture copy of the generated moduleApi helper.
 *
 * This file mirrors the canonical template produced by
 * factory_app/workflows/AppGenerator/tools/module_api_template.py
 * and is used as the static fixture for Playwright acceptance tests.
 *
 * Keep in sync with module_api_template.py.
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
  return (
    err?.error_code === 'ENTITLEMENT_REQUIRED' ||
    err?.code === 'ENTITLEMENT_REQUIRED' ||
    err?.data?.error_code === 'ENTITLEMENT_REQUIRED'
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
    let body = null
    try { body = await response.json() } catch { /* non-JSON body */ }
    const err = new Error(
      body?.error || body?.message ||
      `Module action failed: ${moduleName}.${actionName} ${response.status}`
    )
    if (body?.error_code) err.error_code = body.error_code
    if (body?.code)       err.code       = body.code
    err.status = response.status
    if (body != null) err.data = body
    throw err
  }

  return response.json()
}
