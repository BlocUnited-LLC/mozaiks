/**
 * StudioPage — internal router for all /studio/* paths.
 *
 * Mirrors the admin portal pattern: one registered component handles the
 * entire /studio subtree, with sub-routing done via location.pathname.
 */

import { useLocation } from 'react-router-dom'

import HubPage from './HubPage.jsx'
import StudioHomePage from './StudioHomePage.jsx'
import StudioCreatePage from '../StudioCreatePage.jsx'
import StudioAdaptersPage from './StudioAdaptersPage.jsx'


const STUDIO_ROUTES = {
  '/hub':             HubPage,
  '/studio':          StudioHomePage,
  '/studio/create':   StudioCreatePage,
  '/studio/adapters': StudioAdaptersPage,
}


export default function StudioPage() {
  const location = useLocation()
  const Section = STUDIO_ROUTES[location.pathname] || StudioHomePage
  return <Section />
}
