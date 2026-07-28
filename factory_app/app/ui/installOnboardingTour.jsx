import { createRoot } from 'react-dom/client'
import OnboardingTour from './components/OnboardingTour.jsx'

let installed = false

export function installOnboardingTour() {
  console.log('[installOnboardingTour] called, installed:', installed)
  if (installed || typeof window === 'undefined' || typeof document === 'undefined') return
  installed = true

  const container = document.createElement('div')
  container.setAttribute('data-mozaiks-overlay', 'onboarding-tour')
  document.body.appendChild(container)

  createRoot(container).render(<OnboardingTour />)
}
