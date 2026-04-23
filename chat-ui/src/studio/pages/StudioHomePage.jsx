import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { AdminWorkspaceLayout } from '../../admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  StatusPill,
  SurfaceCard,
  Metric,
  StudioLoadingState,
  StudioErrorState,
} from '../StudioPrimitives.jsx'


export default function StudioHomePage() {
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/home`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) { setSummary(payload); setError(null) }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Studio Home could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  if (loading) return <StudioLoadingState label="Loading Studio Home…" />
  if (error || !summary) return <StudioErrorState title="Studio Home Unavailable" message={error || 'No summary returned.'} />

  const app       = summary.app       || {}
  const ai        = summary.ai        || {}
  const theme     = summary.theme     || {}
  const admin     = summary.admin     || {}
  const workspace = summary.workspace || {}
  const shell     = summary.shell     || {}
  const studio    = summary.studio    || {}
  const home      = summary.home      || {}

  const journeyLabel = app.journey === 'existing_app' ? 'Existing App'
    : app.journey === 'new_app' ? 'New App'
    : 'Not Configured'

  const readinessTone = workspace.runtime_readiness === 'entry_point_configured' ? 'success'
    : workspace.runtime_readiness === 'no_workflows' ? 'warning'
    : 'primary'

  return (
    <AdminWorkspaceLayout>
      <div className="flex flex-col gap-6">

        {/* ── Hero card ─────────────────────────────────────────────────── */}
        <SurfaceCard title={app.name || 'Studio'} eyebrow="Studio Home" accent>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(320px,0.9fr)]">

            <div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill tone="primary">{journeyLabel}</StatusPill>
                <StatusPill tone="success">{studio.local_only ? 'Local Only' : 'Shared Surface'}</StatusPill>
                <StatusPill tone={readinessTone}>{workspace.runtime_readiness || 'unknown'}</StatusPill>
              </div>
              <p className="mt-4 max-w-3xl text-sm leading-7 text-muted-foreground">
                {theme.tagline || 'A local control plane for shaping the next build step, checking runtime readiness, and keeping the workspace app-centric.'}
              </p>
              <div className="mt-5 grid gap-3 sm:grid-cols-3">
                <Metric label="Provider"   value={ai.provider || 'Not configured'} detail={ai.model || 'Choose a default model'} />
                <Metric label="Pages"      value={workspace.page_count ?? 0}       detail={`${workspace.schema_page_count ?? 0} schema · ${workspace.extension_page_count ?? 0} extension`} />
                <Metric label="Workflows"  value={workspace.workflow_count ?? 0}   detail={workspace.entry_point || 'No entry point yet'} />
              </div>
            </div>

            {/* Next step panel */}
            <div className="rounded-3xl border border-border bg-background/75 p-5">
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Next Step</div>
              <p className="mt-4 text-sm leading-7 text-foreground">{home.next_step}</p>
              <dl className="mt-5 space-y-3 text-sm text-muted-foreground">
                <div><span className="font-semibold text-foreground">Route:</span> {studio.route || '/studio'}</div>
                <div><span className="font-semibold text-foreground">Workspace:</span> {studio.workspace_root || 'unknown'}</div>
                <div><span className="font-semibold text-foreground">API billed:</span> {ai.api_billed ? 'yes' : 'no'}</div>
              </dl>
              <div className="mt-5">
                <Link
                  to="/studio/build"
                  className="inline-flex items-center justify-center rounded-2xl border border-primary/30 bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition hover:bg-primary/90"
                >
                  Open Build
                </Link>
              </div>
            </div>
          </div>
        </SurfaceCard>

        {/* ── Secondary cards ────────────────────────────────────────────── */}
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">

          <SurfaceCard title="Workspace Snapshot" eyebrow="Current Shape">
            <div className="grid gap-3 sm:grid-cols-3">
              <Metric label="Header Pages"   value={shell.header_page_count ?? 0}   detail="Shell navigation pills" />
              <Metric label="Header Actions" value={shell.header_action_count ?? 0} detail="From shell.json" />
              <Metric label="Theme"          value={theme.primary || 'Not set'}      detail={theme.logo_alt || 'Logo not configured'} />
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
                  ? (admin.admin_emails?.length ? admin.admin_emails.join(', ') : 'enabled — no emails set')
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
