import { useEffect, useState } from 'react'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  ConsoleErrorState,
  ConsoleLoadingState,
  Metric,
  SurfaceCard,
} from './ConsolePrimitives.jsx'


export default function WorkspaceBillingPage() {
  const [apps, setApps] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/apps`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) {
          setApps(Array.isArray(payload.apps) ? payload.apps : [])
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Billing & hosting could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  if (loading) return <ConsoleLoadingState label="Loading billing & hosting…" />
  if (error) return <ConsoleErrorState title="Billing & Hosting Unavailable" message={error} />

  const activeCount = apps.filter((app) => app.status === 'active').length
  const deployCount = apps.filter((app) => ['review', 'configuring', 'deploying'].includes(app.status)).length

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <SurfaceCard title="Billing & Hosting" eyebrow="Workspace" accent>
          <p className="max-w-3xl text-sm leading-7 text-muted-foreground">
            Billing & Hosting tracks which apps are already active, which ones are still moving toward deployment, and where Mozaiks should surface managed hosting or onboarding prompts next.
          </p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Tracked Apps" value={apps.length} detail="Apps under this workspace" />
            <Metric label="Active" value={activeCount} detail="Apps currently operating" />
            <Metric label="Preparing Deploy" value={deployCount} detail="Review, configuring, or deploying" />
            <Metric label="Self-Managed" value={Math.max(apps.length - activeCount, 0)} detail="Still outside managed hosting" />
          </div>
        </SurfaceCard>
      </div>
    </AdminWorkspaceLayout>
  )
}
