/**
 * OnboardingTour - in-place spotlight tour overlay for the factory app.
 *
 * Mounts as a separate React root via installOnboardingTour.jsx.
 * Step selectors match the factory_app shell.json nav surface.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

// Module API mirrors mozaiks-app pattern but is inlined to avoid cross-package deps.
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

// Steps match module.yaml enum + factory_app shell selectors
const TOUR_STEPS = [
  {
    id: 'create_app',
    selector: 'a[href="/create"]',
    title: 'Create your first app',
    description: 'Describe what you want to build. Mozaiks scaffolds the modules, pages, and deployment for you.',
  },
  {
    id: 'explore_apps',
    selector: 'a[href="/apps"]',
    title: 'Your apps live here',
    description: 'Come back any time to continue builds, check status, and open App Studio.',
  },
  {
    id: 'open_support',
    selector: 'a[href="/support"], button[data-id="support"]',
    title: 'Get help any time',
    description: 'Open a support conversation directly from the profile menu.',
  },
]

const PADDING = 8

function getRect(selector) {
  const el = document.querySelector(selector)
  if (!el) return null
  const r = el.getBoundingClientRect()
  return {
    top: r.top - PADDING,
    left: r.left - PADDING,
    width: r.width + PADDING * 2,
    height: r.height + PADDING * 2,
    bottom: r.bottom + PADDING,
    right: r.right + PADDING,
    centerX: r.left + r.width / 2,
  }
}

function Backdrop({ rect }) {
  if (!rect) return null
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0" style={{ zIndex: 9998 }}>
      <div className="absolute bg-black/60" style={{ top: 0, left: 0, right: 0, height: rect.top }} />
      <div className="absolute bg-black/60" style={{ top: rect.bottom, left: 0, right: 0, bottom: 0 }} />
      <div className="absolute bg-black/60" style={{ top: rect.top, left: 0, width: rect.left, height: rect.height }} />
      <div className="absolute bg-black/60" style={{ top: rect.top, left: rect.right, right: 0, height: rect.height }} />
      <div
        className="absolute rounded-lg ring-2 ring-primary/80"
        style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
      />
    </div>
  )
}

function Tooltip({ rect, step, stepIndex, totalSteps, onNext, onSkip, acting }) {
  if (!rect) return null
  const vw = window.innerWidth
  const tooltipWidth = Math.min(280, vw - 32)
  const spaceBelow = window.innerHeight - rect.bottom
  const positionAbove = spaceBelow < 160
  let left = rect.centerX - tooltipWidth / 2
  left = Math.max(16, Math.min(left, vw - tooltipWidth - 16))
  const top = positionAbove ? rect.top - 8 : rect.bottom + 12

  return (
    <div
      role="dialog"
      aria-label={`Onboarding step ${stepIndex + 1} of ${totalSteps}`}
      className="fixed flex flex-col gap-3 rounded-xl border border-border bg-card p-4 shadow-xl shadow-black/30"
      style={{ zIndex: 9999, width: tooltipWidth, left, top, transform: positionAbove ? 'translateY(-100%)' : undefined }}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-muted-foreground">{stepIndex + 1} / {totalSteps}</span>
        <button type="button" onClick={onSkip} disabled={acting} className="text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50">
          Skip tour
        </button>
      </div>
      <div>
        <p className="text-sm font-semibold text-foreground">{step.title}</p>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">{step.description}</p>
      </div>
      <div className="flex items-center gap-1.5">
        {Array.from({ length: totalSteps }).map((_, i) => (
          <div key={i} className={`h-1.5 rounded-full transition-all ${i === stepIndex ? 'w-4 bg-primary' : 'w-1.5 bg-muted-foreground/30'}`} />
        ))}
      </div>
      <button
        type="button"
        onClick={onNext}
        disabled={acting}
        className="w-full rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:opacity-50"
      >
        {acting ? 'Saving...' : stepIndex < totalSteps - 1 ? 'Next' : 'Done'}
      </button>
    </div>
  )
}

export default function OnboardingTour() {
  const [ready, setReady] = useState(false)
  const [stepIndex, setStepIndex] = useState(0)
  const [rect, setRect] = useState(null)
  const [acting, setActing] = useState(false)
  const [visible, setVisible] = useState(false)
  const intervalRef = useRef(null)

  useEffect(() => {
    moduleAction('user_onboarding', 'get_onboarding_status', {})
      .then((status) => {
        if (!status || status.dismissed || status.progress === 100) return
        const steps = status.steps || {}
        const firstIncomplete = TOUR_STEPS.findIndex((s) => !steps[s.id]?.completed)
        if (firstIncomplete === -1) return
        setStepIndex(firstIncomplete)
        setReady(true)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!ready) return
    function update() {
      const step = TOUR_STEPS[stepIndex]
      if (!step) return
      const r = getRect(step.selector)
      setRect(r)
      setVisible(Boolean(r))
    }
    update()
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    intervalRef.current = setInterval(update, 400)
    return () => {
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
      clearInterval(intervalRef.current)
    }
  }, [ready, stepIndex])

  const handleNext = useCallback(async () => {
    const step = TOUR_STEPS[stepIndex]
    setActing(true)
    try { await moduleAction('user_onboarding', 'complete_step', { step_id: step.id }) } catch { /* best effort */ }
    setActing(false)
    const next = stepIndex + 1
    if (next >= TOUR_STEPS.length) { setVisible(false); setReady(false) }
    else setStepIndex(next)
  }, [stepIndex])

  const handleSkip = useCallback(async () => {
    setActing(true)
    try { await moduleAction('user_onboarding', 'dismiss_onboarding', {}) } catch { /* best effort */ }
    setVisible(false); setReady(false); setActing(false)
  }, [])

  if (!visible || !ready) return null

  return (
    <>
      <Backdrop rect={rect} />
      <Tooltip rect={rect} step={TOUR_STEPS[stepIndex]} stepIndex={stepIndex} totalSteps={TOUR_STEPS.length} onNext={handleNext} onSkip={handleSkip} acting={acting} />
    </>
  )
}
