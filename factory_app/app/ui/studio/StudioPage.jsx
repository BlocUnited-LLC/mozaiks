/**
 * StudioPage — internal router for all /studio/* paths.
 *
 * Mirrors the admin portal pattern: one registered component handles the
 * entire /studio subtree, with sub-routing done via location.pathname.
 */

import { useLocation } from 'react-router-dom'

import StudioHomePage from './pages/StudioHomePage.jsx'
import StudioCreatePage from '../pages/custom/StudioCreatePage.jsx'
import StudioAdaptersPage from './pages/StudioAdaptersPage.jsx'


const STUDIO_ROUTES = {
  '/studio': StudioHomePage,
  '/studio/create': StudioCreatePage,
  '/studio/adapters': StudioAdaptersPage,
}


export default function StudioPage() {
  const location = useLocation()
  const Section = STUDIO_ROUTES[location.pathname] || StudioHomePage
  return <Section />
}