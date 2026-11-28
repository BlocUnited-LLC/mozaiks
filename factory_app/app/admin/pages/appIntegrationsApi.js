import { studioFetch } from './studioApi.js'

export async function requestConnectors(appId, { method = 'GET', service, check = false, body, signal } = {}) {
  const suffix = service ? '/' + encodeURIComponent(service) : ''
  const path = '/api/studio/integrations/connectors' + suffix + (check ? '/health-check' : '')
  const query = new URLSearchParams({ dashboard_app_id: appId })
  const response = await studioFetch(path + '?' + query, {
    method, signal,
    ...(body && { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    if (response.status === 403 || response.status === 404) throw new Error('Integration or app unavailable to this account.')
    if (response.status === 503) throw new Error('The credential store is unavailable. Try again after it is configured.')
    throw new Error(`Integration request failed (HTTP ${response.status}).`)
  }
  return response.json()
}

