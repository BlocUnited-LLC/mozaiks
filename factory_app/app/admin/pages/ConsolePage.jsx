/**
 * ConsolePage — internal router for first-party app-console paths.
 *
 * Mirrors the admin portal pattern: one registered component can handle the
 * first-party app-console subtree, with sub-routing done via location.pathname.
 */

import { Navigate, useLocation } from 'react-router-dom'

import AppsPage from './AppsPage.jsx'
import AppOverviewPage from './AppOverviewPage.jsx'
import AppBuildHistoryPage from './AppBuildHistoryPage.jsx'
import AppIntegrationsPage from './AppIntegrationsPage.jsx'
import CreateAppRedirectPage from './CreateAppRedirectPage.jsx'


export default function ConsolePage() {
  const location = useLocation()

  if (/^\/apps\/[^/]+$/.test(location.pathname)) {
    return <Navigate replace to={`${location.pathname}/overview`} />
  }

  let Section = AppOverviewPage

  if (location.pathname === '/apps') {
    Section = AppsPage
  } else if (location.pathname === '/apps/new') {
    Section = CreateAppRedirectPage
  } else if (/^\/apps\/[^/]+\/activity$/.test(location.pathname)) {
    Section = AppBuildHistoryPage
  } else if (/^\/apps\/[^/]+\/integrations$/.test(location.pathname)) {
    Section = AppIntegrationsPage
  } else if (/^\/apps\/[^/]+\/overview$/.test(location.pathname)) {
    Section = AppOverviewPage
  }

  return <Section />
}
