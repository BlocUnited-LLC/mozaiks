/**
 * ConsolePage — internal router for first-party app-console paths.
 *
 * Mirrors the admin portal pattern: one registered component can handle the
 * first-party app-console subtree, with sub-routing done via location.pathname.
 */

import { useLocation } from 'react-router-dom'

import AppsPage from './AppsPage.jsx'
import AppOverviewPage from './AppOverviewPage.jsx'
import AppBuildPage from './AppBuildPage.jsx'
import AppIntegrationsPage from './AppIntegrationsPage.jsx'
import AppDeployPage from './AppDeployPage.jsx'
import CreateAppRedirectPage from './CreateAppRedirectPage.jsx'
import WorkspaceBillingPage from './WorkspaceBillingPage.jsx'
import WorkspaceOperationsPage from './WorkspaceOperationsPage.jsx'
import WorkspaceSettingsPage from './WorkspaceSettingsPage.jsx'
import WorkspaceUsagePage from './WorkspaceUsagePage.jsx'


export default function ConsolePage() {
  const location = useLocation()
  let Section = AppOverviewPage

  if (location.pathname === '/apps') {
    Section = AppsPage
  } else if (location.pathname === '/apps/new') {
    Section = CreateAppRedirectPage
  } else if (location.pathname === '/usage') {
    Section = WorkspaceUsagePage
  } else if (location.pathname === '/operations') {
    Section = WorkspaceOperationsPage
  } else if (location.pathname === '/billing') {
    Section = WorkspaceBillingPage
  } else if (location.pathname === '/settings') {
    Section = WorkspaceSettingsPage
  } else if (/^\/apps\/[^/]+\/build$/.test(location.pathname)) {
    Section = AppBuildPage
  } else if (/^\/apps\/[^/]+\/deploy$/.test(location.pathname)) {
    Section = AppDeployPage
  } else if (/^\/apps\/[^/]+\/integrations$/.test(location.pathname)) {
    Section = AppIntegrationsPage
  } else if (/^\/apps\/[^/]+\/overview$/.test(location.pathname)) {
    Section = AppOverviewPage
  }

  return <Section />
}
