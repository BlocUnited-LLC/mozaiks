import { studioFetch } from './studioApi.js'

export function getDashboardSurface(payload, scope = 'app') {
  if (payload?.surface?.scope === scope) return payload.surface
  const surface = payload?.[scope]
  return surface && typeof surface === 'object' ? surface : null
}

export function getDefaultPortalRoute(payload, scope = 'app') {
  const surface = getDashboardSurface(payload, scope)
  const defaultPortalId = surface?.default_portal
  const portals = Array.isArray(surface?.portals) ? surface.portals : []
  const portal = portals.find((item) => (
    item?.id === defaultPortalId &&
    item?.enabled !== false &&
    typeof item?.route === 'string' &&
    item.route.startsWith('/')
  ))
  return portal?.route || null
}

export function getSurfaceRoutePattern(payload, scope = 'app') {
  const surface = getDashboardSurface(payload, scope)
  const routePattern = surface?.route_pattern
  return typeof routePattern === 'string' && routePattern.startsWith('/') ? routePattern : null
}

export function buildAppDashboardHref(routePattern, appId) {
  if (!routePattern || !appId) return null
  return String(routePattern).replace(':appId', encodeURIComponent(appId))
}

export function resolveStudioApp(apps, appId, buildRegistryId = null) {
  const app = apps?.find((entry) => entry.app_id === appId)
  if (!app) throw new Error('App not found.')
  if (buildRegistryId && app.build_registry_id !== buildRegistryId) {
    throw new Error('App build association does not match the registry.')
  }
  return app
}

export async function fetchDashboardConfig({ scope = null, signal } = {}) {
  const params = new URLSearchParams()
  if (scope) params.set('scope', scope)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  const response = await studioFetch(`/api/studio/dashboard${suffix}`, {
    headers: {
      Accept: 'application/json',
    },
    signal,
  })
  if (!response.ok) {
    throw new Error(`Dashboard manifest unavailable: ${response.status}`)
  }
  return response.json()
}
