import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  StatusPill,
  SurfaceCard,
  Metric,
  ConsoleLoadingState,
  ConsoleErrorState,
} from './ConsolePrimitives.jsx'


export default function AppOverviewPage() {
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
        if (!cancelled) { setSummary(payload); setError(null) }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'App overview could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  if (loading) return <ConsoleLoadingState label="Loading App Overview…" />
  if (error || !summary) return <ConsoleErrorState title="App Overview Unavailable" message={error || 'No summary returned.'} />

  const app = summary.app || {}
  const ai = summary.ai || {}
  const theme = summary.theme || {}
  const admin = summary.admin || {}
  const workspace = summary.workspace || {}
  const shell = summary.shell || {}
  const consoleSurface = summary.console || {}
  const home = summary.home || {}

  const journeyLabel = app.journey === 'brownfield_app' ? 'Brownfield App'
    : app.journey === 'greenfield_app' ? 'Greenfield App'
    : 'App Console'
  const lifecycleLabel = app.lifecycle_label || 'Draft'

  const readinessTone = workspace.runtime_readiness === 'entry_point_configured' ? 'success'
    : workspace.runtime_readiness === 'no_workflows' ? 'warning'
    : 'primary'

  return (
    <AdminWorkspaceLayout>
      <div className="flex flex-col gap-6">
        <SurfaceCard title={app.name || 'App Overview'} eyebrow="Overview" accent>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(320px,0.9fr)]">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill tone="primary">{lifecycleLabel}</StatusPill>
                <StatusPill tone="default">{journeyLabel}</StatusPill>
                <StatusPill tone={readinessTone}>{workspace.runtime_readiness || 'unknown'}</StatusPill>
              </div>
              <p className="mt-4 max-w-3xl text-sm leading-7 text-muted-foreground">
                {app.description || theme.tagline || 'Track lifecycle state, current configuration, and the next recommended build step from one app console.'}
              </p>
              <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Metric label="Users" value={app.lifecycle_state === 'active' ? '—' : 'Pending'} detail="Visible after deployment" />
                <Metric label="Revenue" value={app.lifecycle_state === 'active' ? '—' : 'Pending'} detail="App-level reporting" />
                <Metric label="Token Usage" value={app.lifecycle_state === 'active' ? '—' : 'Pending'} detail="App-scoped runtime cost" />
                <Metric label="Active Workflows" value={workspace.workflow_count ?? 0} detail={workspace.entry_point || 'No entry point yet'} />
              </div>
            </div>

            <div className="rounded-3xl border border-border bg-background/75 p-5">
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Lifecycle</div>
              <p className="mt-3 text-sm font-semibold text-foreground">{lifecycleLabel}</p>
              <p className="mt-4 text-sm leading-7 text-foreground">{home.next_step}</p>
              <dl className="mt-5 space-y-3 text-sm text-muted-foreground">
                <div><span className="font-semibold text-foreground">Route:</span> {consoleSurface.route || `/apps/${appId}/overview`}</div>
                <div><span className="font-semibold text-foreground">Workspace:</span> {consoleSurface.workspace_root || 'unknown'}</div>
                <div><span className="font-semibold text-foreground">Runtime Health:</span> {app.lifecycle_state === 'active' ? 'Monitoring' : 'Not live yet'}</div>
                <div><span className="font-semibold text-foreground">AI Provider:</span> {ai.provider || 'Not configured'}</div>
              </dl>
              <div className="mt-5">
                <Link
                  to={`/apps/${appId}/build`}
                  className="inline-flex items-center justify-center rounded-2xl border border-primary/30 bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition hover:bg-primary/90"
                >
                  Open Build
                </Link>
              </div>
            </div>
          </div>
        </SurfaceCard>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <SurfaceCard title="Workspace Snapshot" eyebrow="Current Shape">
            <div className="grid gap-3 sm:grid-cols-3">
              <Metric label="Deployment" value={lifecycleLabel} detail="Current app lifecycle state" />
              <Metric label="Header Pages" value={shell.header_page_count ?? 0} detail="Shell navigation pills" />
              <Metric label="Header Actions" value={shell.header_action_count ?? 0} detail="From shell.json" />
              <Metric label="Theme" value={theme.primary || 'Not set'} detail={theme.logo_alt || 'Logo not configured'} />
            </div>
            {app.first_goal && (
              <div className="mt-5 rounded-2xl border border-border bg-muted/40 p-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">First Goal</div>
                <p className="mt-2 text-sm leading-7 text-foreground">{app.first_goal}</p>
              </div>
            )}
          </SurfaceCard>

          <SurfaceCard title="App Configuration" eyebrow="Setup">
            <dl className="space-y-4 text-sm leading-7 text-muted-foreground">
              <div>
                <span className="font-semibold text-foreground">Admin: </span>
                {admin.enabled
                  ? (admin.admins?.length ? admin.admins.join(', ') : 'framework shell active — no admins set')
                  : 'not enabled'}
              </div>
              {app.existing_app_url && (
                <div>
                  <span className="font-semibold text-foreground">Existing App: </span>
                  <a className="text-primary underline-offset-4 hover:underline" href={app.existing_app_url} target="_blank" rel="noreferrer">
                    {app.existing_app_url}
                  </a>
                </div>
              )}
              <div>
                <span className="font-semibold text-foreground">Host-Owned: </span>
                {app.host_owned_summary || 'Capture what should remain outside Mozaiks before broader generation.'}
              </div>
            </dl>
          </SurfaceCard>
        </div>
      </div>
    </AdminWorkspaceLayout>
  )
}
