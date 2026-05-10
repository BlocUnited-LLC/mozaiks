import { useEffect, useState } from 'react'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  ConsoleErrorState,
  ConsoleLoadingState,
  Metric,
  SurfaceCard,
} from './ConsolePrimitives.jsx'


export default function WorkspaceSettingsPage() {
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
        if (!cancelled) setError(err instanceof Error ? err.message : 'Workspace settings could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  if (loading) return <ConsoleLoadingState label="Loading workspace settings…" />
  if (error) return <ConsoleErrorState title="Workspace Settings Unavailable" message={error} />

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <SurfaceCard title="Workspace Settings" eyebrow="Workspace" accent>
          <p className="max-w-3xl text-sm leading-7 text-muted-foreground">
            Workspace Settings is the workspace-level home for team defaults, governance, and shared operating preferences. App-specific configuration stays inside each App Console.
          </p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Tracked Apps" value={apps.length} detail="Apps under this workspace" />
            <Metric label="Active Apps" value={apps.filter((app) => app.status === 'active').length} detail="Currently operating apps" />
            <Metric label="Build Queue" value={apps.filter((app) => ['draft', 'building', 'review', 'configuring'].includes(app.status)).length} detail="Apps still moving through build" />
            <Metric label="Revisions" value={apps.filter((app) => app.status === 'needs_revision').length} detail="Apps waiting on a requested change" />
          </div>
        </SurfaceCard>
      </div>
    </AdminWorkspaceLayout>
  )
}
