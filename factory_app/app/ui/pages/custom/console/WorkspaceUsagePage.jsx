import { useEffect, useState } from 'react'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  ConsoleErrorState,
  ConsoleLoadingState,
  Metric,
  StatusPill,
  SurfaceCard,
} from './ConsolePrimitives.jsx'


export default function WorkspaceUsagePage() {
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
        if (!cancelled) setError(err instanceof Error ? err.message : 'Workspace usage could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  if (loading) return <ConsoleLoadingState label="Loading workspace usage…" />
  if (error) return <ConsoleErrorState title="Workspace Usage Unavailable" message={error} />

  const activeCount = apps.filter((app) => app.status === 'active').length
  const buildCount = apps.filter((app) => ['draft', 'building', 'review', 'configuring'].includes(app.status)).length
  const revisionCount = apps.filter((app) => app.status === 'needs_revision').length

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <SurfaceCard title="Usage" eyebrow="Workspace" accent>
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone="primary">App-level aggregation</StatusPill>
            <StatusPill tone="success">{apps.length} tracked apps</StatusPill>
          </div>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-muted-foreground">
            Usage in the Mozaiks Console is app-scoped. Workflow-level details stay inside Build and Admin, while this surface summarizes which apps are active, still building, or waiting on revision.
          </p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Tracked Apps" value={apps.length} detail="App records in this workspace" />
            <Metric label="Active Apps" value={activeCount} detail="Live or operating apps" />
            <Metric label="In Build" value={buildCount} detail="Draft, building, review, or configuring" />
            <Metric label="Needs Revision" value={revisionCount} detail="Apps blocked on a requested change" />
          </div>
        </SurfaceCard>
      </div>
    </AdminWorkspaceLayout>
  )
}
