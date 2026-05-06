/**
 * HubPage — My Apps hub.
 *
 * Lists Studio-visible app entries for the current workspace.
 * Each card routes into the right Studio surface for that app.
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  API_BASE,
  SurfaceCard,
  StatusPill,
  Metric,
  ActionButton,
  StudioLoadingState,
  StudioErrorState,
} from './StudioPrimitives.jsx'
import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'


const STATUS_TONE = {
  pending:           'default',
  building:          'primary',
  generated:         'success',
  failed:            'warning',
  hosting_requested: 'primary',
  hosted:            'success',
}

const STATUS_LABEL = {
  pending:           'Pending',
  building:          'Building',
  generated:         'Generated',
  failed:            'Failed',
  hosting_requested: 'Hosting Requested',
  hosted:            'Hosted',
}

const IN_PROGRESS_STATUSES = new Set(['pending', 'building', 'hosting_requested'])
const READY_STATUSES = new Set(['generated', 'hosted'])


function AppCard({ app, onOpen }) {
  const tone = STATUS_TONE[app.status] ?? 'default'
  const label = STATUS_LABEL[app.status] ?? app.status
  const destinationLabel = IN_PROGRESS_STATUSES.has(app.status) ? 'Continue build' : 'Open Studio'

  return (
    <button
      type="button"
      onClick={() => onOpen(app)}
      className="flex w-full cursor-pointer flex-col gap-4 rounded-3xl border border-border bg-card p-5 text-left transition-all hover:border-primary/40 hover:bg-background/80 hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-primary/20 bg-primary/10 text-lg font-bold text-primary">
          {(app.name || 'A').charAt(0).toUpperCase()}
        </div>
        <StatusPill tone={tone}>{label}</StatusPill>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-foreground tracking-wide">{app.name}</h3>
        {app.description && (
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground line-clamp-2">{app.description}</p>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between gap-3 text-xs">
        <span className="text-muted-foreground">
          {app.created_label || (app.created_at
            ? new Date(app.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
            : 'Just created')}
        </span>
        <span className="font-semibold text-primary">{destinationLabel}</span>
      </div>
    </button>
  )
}


function EmptyState({ onCreate }) {
  return (
    <div className="rounded-3xl border border-dashed border-border bg-background/70 px-8 py-14">
      <div className="mx-auto flex max-w-xl flex-col items-center text-center">
        <StatusPill tone="primary">Ready to start</StatusPill>
        <h3 className="mt-4 text-lg font-semibold text-foreground">Create the first app for this workspace</h3>
        <p className="mt-3 max-w-md text-sm leading-7 text-muted-foreground">
          Start with a short request, then let Studio route the build and refinement steps without turning the hub into a second full editor.
        </p>
        <div className="mt-6">
          <ActionButton onClick={onCreate}>Start New App</ActionButton>
        </div>
      </div>
    </div>
  )
}


export default function HubPage() {
  const navigate = useNavigate()
  const [apps, setApps] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const totalApps = apps.length
  const inProgressCount = apps.filter((app) => IN_PROGRESS_STATUSES.has(app.status)).length
  const readyCount = apps.filter((app) => READY_STATUSES.has(app.status)).length

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/apps`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const data = await res.json()
        if (!cancelled) { setApps(Array.isArray(data.apps) ? data.apps : []); setError(null) }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load your apps.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  function handleOpen(app) {
    const dest = app.destination || (app.status === 'pending' || app.status === 'building'
      ? '/studio/create'
      : '/studio')
    navigate(dest)
  }

  function handleCreate() {
    navigate('/create')
  }

  if (loading) return <StudioLoadingState label="Loading your apps…" />

  return (
    <AdminWorkspaceLayout>
      <div className="flex flex-col gap-6">
        <SurfaceCard title="My Apps" eyebrow="Hub" accent>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.95fr)]">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill tone="primary">Workspace Catalog</StatusPill>
                <StatusPill tone={totalApps > 0 ? 'success' : 'warning'}>
                  {totalApps > 0 ? `${totalApps} tracked` : 'No apps yet'}
                </StatusPill>
              </div>
              <p className="mt-4 max-w-2xl text-sm leading-7 text-muted-foreground">
                Review existing app builds, reopen the right Studio surface, or start a fresh build without duplicating the full create flow inside the hub.
              </p>
              <div className="mt-5">
                <ActionButton onClick={handleCreate}>Start New App</ActionButton>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
              <Metric label="Apps" value={totalApps} detail="Build registry records" />
              <Metric label="In Progress" value={inProgressCount} detail="Pending or building" />
              <Metric label="Ready" value={readyCount} detail="Generated or hosted" />
            </div>
          </div>
        </SurfaceCard>

        {error ? (
          <StudioErrorState title="Could not load apps" message={error} />
        ) : apps.length === 0 ? (
          <EmptyState onCreate={handleCreate} />
        ) : (
          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
            {apps.map((app) => (
              <AppCard key={app.build_registry_id} app={app} onOpen={handleOpen} />
            ))}
          </div>
        )}
      </div>
    </AdminWorkspaceLayout>
  )
}
