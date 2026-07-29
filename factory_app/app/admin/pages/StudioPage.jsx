/**
 * StudioPage — redirect helper for first-party Studio paths.
 *
 * Concrete Studio pages are declared in app/ui/route_manifest.json. This
 * component preserves the app-root redirect from /apps/:appId to the
 * manifest-declared default App Dashboard portal.
 */

import { useEffect, useMemo, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { StudioLoadingState } from '../../ui/components/StudioShared.jsx'
import {
  buildAppDashboardHref,
  fetchDashboardConfig,
  getDefaultPortalRoute,
} from './dashboardRoutes.js'

function decodeRouteSegment(value) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

export default function StudioPage() {
  const location = useLocation()
  const appRootMatch = useMemo(() => /^\/apps\/([^/]+)$/.exec(location.pathname), [location.pathname])
  const appId = appRootMatch ? decodeRouteSegment(appRootMatch[1]) : null
  const [redirectState, setRedirectState] = useState({
    loaded: !appId,
    href: null,
  })

  useEffect(() => {
    if (!appId) {
      setRedirectState({ loaded: true, href: null })
      return undefined
    }

    const controller = new AbortController()
    setRedirectState({ loaded: false, href: null })
    fetchDashboardConfig({ signal: controller.signal })
      .then((payload) => {
        const route = getDefaultPortalRoute(payload, 'app')
        setRedirectState({
          loaded: true,
          href: buildAppDashboardHref(route, appId),
        })
      })
      .catch((err) => {
        if (err?.name !== 'AbortError') {
          console.error('[StudioPage] dashboard manifest load failed:', err)
          setRedirectState({ loaded: true, href: null })
        }
      })
    return () => controller.abort()
  }, [appId])

  if (appId && !redirectState.loaded) {
    return <StudioLoadingState label="Opening app dashboard..." />
  }

  if (appId && redirectState.href) return <Navigate replace to={redirectState.href} />

  return <Navigate replace to="/apps" />
}
