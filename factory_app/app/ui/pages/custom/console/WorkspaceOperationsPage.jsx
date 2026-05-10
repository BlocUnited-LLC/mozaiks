import { useEffect, useState } from 'react'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  ConsoleErrorState,
  ConsoleLoadingState,
  StatusPill,
  SurfaceCard,
} from './ConsolePrimitives.jsx'


function toneForStatus(status) {
  if (status === 'active') return 'success'
  if (status === 'needs_revision') return 'warning'
  if (status === 'archived') return 'default'
  return 'primary'
}


export default function WorkspaceOperationsPage() {
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
        if (!cancelled) setError(err instanceof Error ? err.message : 'Workspace operations could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  if (loading) return <ConsoleLoadingState label="Loading workspace operations…" />
  if (error) return <ConsoleErrorState title="Workspace Operations Unavailable" message={error} />

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <SurfaceCard title="Operations" eyebrow="Workspace" accent>
          <p className="max-w-3xl text-sm leading-7 text-muted-foreground">
            Operations summarizes app lifecycle health, revision blockers, and deployment readiness across the workspace. Runtime and workflow diagnostics remain available in Build and Admin for each app.
          </p>
          <div className="mt-5 space-y-3">
            {apps.length === 0 ? (
              <div className="rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm text-muted-foreground">
                No app records exist yet.
              </div>
            ) : apps.map((app) => (
              <div key={app.build_registry_id} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-background/70 px-4 py-3">
                <div>
                  <div className="text-sm font-semibold text-foreground">{app.name}</div>
                  <div className="text-sm text-muted-foreground">{app.description || 'No description yet.'}</div>
                </div>
                <StatusPill tone={toneForStatus(app.status)}>{app.lifecycle_label || app.status}</StatusPill>
              </div>
            ))}
          </div>
        </SurfaceCard>
      </div>
    </AdminWorkspaceLayout>
  )
}
