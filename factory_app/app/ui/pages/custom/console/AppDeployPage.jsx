import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  ConsoleErrorState,
  ConsoleLoadingState,
  Metric,
  StatusPill,
  SurfaceCard,
} from './ConsolePrimitives.jsx'


export default function AppDeployPage() {
  const { appId = 'workspace-app' } = useParams()
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/overview?app_id=${encodeURIComponent(appId)}`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) {
          setSummary(payload)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Deploy could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [appId])

  if (loading) return <ConsoleLoadingState label="Loading deploy surface…" />
  if (error || !summary) return <ConsoleErrorState title="Deploy Unavailable" message={error || 'No deploy summary returned.'} />

  const app = summary.app || {}
  const lifecycleLabel = app.lifecycle_label || 'Draft'
  const readyToDeploy = ['review', 'configuring', 'deploying', 'active'].includes(app.lifecycle_state)

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <SurfaceCard title="Deploy" eyebrow="App Console" accent>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={readyToDeploy ? 'success' : 'warning'}>{lifecycleLabel}</StatusPill>
            <StatusPill tone="primary">Billing & Hosting</StatusPill>
          </div>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-muted-foreground">
            Deploy collects release readiness, billing & hosting expectations, and the next required action before the app moves into an active operating state.
          </p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Lifecycle" value={lifecycleLabel} detail="Current release position" />
            <Metric label="Deploy Ready" value={readyToDeploy ? 'Yes' : 'Not yet'} detail="Requires review or configuration first" />
            <Metric label="Hosting" value={app.lifecycle_state === 'active' ? 'Managed' : 'Pending'} detail="Billing & hosting onboarding" />
            <Metric label="Operations" value={app.lifecycle_state === 'active' ? 'Monitoring' : 'Not live yet'} detail="Runtime health becomes primary after activation" />
          </div>
        </SurfaceCard>
      </div>
    </AdminWorkspaceLayout>
  )
}
