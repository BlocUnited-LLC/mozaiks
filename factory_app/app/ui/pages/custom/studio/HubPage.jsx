/**
 * HubPage — My Apps hub.
 *
 * Lists all build registry records for the current user from the app_registry module.
 * Each card links into the Studio for that app.
 * Works for both OSS (single app, goes straight to studio) and hosted
 * platform users (multiple apps, pick one first).
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  API_BASE,
  SurfaceCard,
  StatusPill,
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


function AppCard({ app, onOpen }) {
  const tone = STATUS_TONE[app.status] ?? 'default'
  const label = STATUS_LABEL[app.status] ?? app.status

  return (
    <button
      type="button"
      onClick={() => onOpen(app)}
      className="w-full text-left rounded-3xl border border-border bg-card p-5 flex flex-col gap-4 hover:border-primary/40 hover:shadow-md transition-all cursor-pointer"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="w-11 h-11 rounded-xl bg-primary/10 border border-primary/20 flex items-center justify-center text-lg font-bold text-primary shrink-0">
          {(app.name || 'A').charAt(0).toUpperCase()}
        </div>
        <StatusPill tone={tone}>{label}</StatusPill>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-foreground tracking-wide">{app.name}</h3>
        {app.description && (
          <p className="mt-1 text-xs text-muted-foreground leading-relaxed line-clamp-2">{app.description}</p>
        )}
      </div>

      <div className="mt-auto text-xs text-muted-foreground">
        {app.created_at
          ? new Date(app.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
          : 'Just created'}
      </div>
    </button>
  )
}


function EmptyState({ onCreate }) {
  return (
    <div className="rounded-3xl border-2 border-dashed border-border bg-card/60 px-8 py-16 text-center">
      <div className="text-4xl mb-4 text-muted-foreground">✦</div>
      <h3 className="text-base font-semibold text-foreground mb-2 tracking-wide uppercase">No apps yet</h3>
      <p className="text-sm text-muted-foreground mb-6 max-w-xs mx-auto leading-relaxed">
        Build your first app with the AI factory — describe what you want and the pipeline takes care of the rest.
      </p>
      <ActionButton onClick={onCreate}>Build your first app</ActionButton>
    </div>
  )
}


export default function HubPage() {
  const navigate = useNavigate()
  const [apps, setApps] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/modules/app_registry/list_user_apps?limit=50`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const data = await res.json()
        if (!cancelled) { setApps(data.apps || []); setError(null) }
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
    const dest = app.status === 'pending' || app.status === 'building'
      ? '/studio/create'
      : '/studio'
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
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground max-w-lg leading-7">
              All apps you've built on this workspace. Click an app to open its Studio, or start a new one.
            </p>
            <ActionButton onClick={handleCreate}>+ New App</ActionButton>
          </div>
        </SurfaceCard>

        {error ? (
          <StudioErrorState title="Could not load apps" message={error} />
        ) : apps.length === 0 ? (
          <EmptyState onCreate={handleCreate} />
        ) : (
          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
            {apps.map(app => (
              <AppCard key={app.build_registry_id} app={app} onOpen={handleOpen} />
            ))}
          </div>
        )}
      </div>
    </AdminWorkspaceLayout>
  )
}
