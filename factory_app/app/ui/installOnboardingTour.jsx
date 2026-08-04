import { createRoot } from 'react-dom/client'
import onboardingConfigSource from '../config/onboarding.yaml?raw'
import { OnboardingTour, parseOnboardingConfigSource } from '@mozaiks/chat-ui/ui'

let installed = false

async function moduleAction(moduleName, actionName, input = {}) {
  const token =
    window.mozaiksAuth?.getAccessToken?.() ||
    sessionStorage.getItem('mozaiks_access_token') ||
    localStorage.getItem('mozaiks_access_token') ||
    localStorage.getItem('chatui_token') ||
    localStorage.getItem('access_token')

  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`/api/modules/${moduleName}/${actionName}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(input),
  })
  if (!res.ok) throw new Error(`${moduleName}.${actionName} ${res.status}`)
  return res.json()
}

export function installOnboardingTour() {
  if (installed || typeof window === 'undefined' || typeof document === 'undefined') return
  installed = true

  let onboardingConfig = null
  try {
    onboardingConfig = parseOnboardingConfigSource(onboardingConfigSource) || null
  } catch (error) {
    console.warn('[factory_app/ui] Failed to parse onboarding.yaml', error)
    onboardingConfig = null
  }

  const steps = Array.isArray(onboardingConfig?.steps) ? onboardingConfig.steps : []
  if (steps.length === 0) return

  const container = document.createElement('div')
  container.setAttribute('data-mozaiks-overlay', 'onboarding-tour')
  document.body.appendChild(container)

  createRoot(container).render(
    <OnboardingTour
      config={onboardingConfig}
      loadStatus={() => moduleAction('user_onboarding', 'get_onboarding_status', {})}
      completeStep={(stepId) => moduleAction('user_onboarding', 'complete_step', { step_id: stepId })}
      dismissTour={() => moduleAction('user_onboarding', 'dismiss_onboarding', {})}
    />
  )
}
