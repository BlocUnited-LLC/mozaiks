/**
 * StudioPage — redirect helper for first-party Studio paths.
 *
 * Concrete Studio pages are declared in app/ui/route_manifest.json. This
 * component only preserves the app-root redirect from /apps/:appId to the
 * canonical overview route.
 */

import { Navigate, useLocation } from 'react-router-dom'


export default function StudioPage() {
  const location = useLocation()

  if (/^\/apps\/[^/]+$/.test(location.pathname)) {
    return <Navigate replace to={`${location.pathname}/overview`} />
  }

  return <Navigate replace to="/apps" />
}
