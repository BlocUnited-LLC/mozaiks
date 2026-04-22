import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { BuilderWorkspaceLayout } from '../components/BuilderWorkspaceNav.jsx'


const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
  ''


function StatusPill({ children, tone = 'default' }) {
  const tones = {
    default: 'border-border bg-muted text-muted-foreground',
    primary: 'border-primary/30 bg-primary/10 text-primary',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
  }

  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] ${tones[tone]}`}>
      {children}
    </span>
  )
}


function SurfaceCard({ title, eyebrow, children, accent = false, className = '' }) {
  return (
    <section
      className={`rounded-3xl border ${accent ? 'border-primary/30 bg-gradient-to-br from-primary/10 via-card to-secondary/10' : 'border-border bg-card'} p-6 shadow-sm ${className}`}
    >
      {eyebrow ? (
        <div className="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-muted-foreground">{eyebrow}</div>
      ) : null}
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  )
}


function Metric({ label, value, detail = null }) {
  return (
    <div className="rounded-2xl border border-border bg-background/70 p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-foreground">{value}</div>
      {detail ? <div className="mt-1 text-sm text-muted-foreground">{detail}</div> : null}
    </div>
  )
}


function LoadingState() {
  return (
    <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10">
      <div className="flex items-center gap-3 rounded-2xl border border-border bg-card px-5 py-4 text-sm text-muted-foreground shadow-sm">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        Loading Studio Home...
      </div>
    </div>
  )
}


function ErrorState({ message }) {
  return (
    <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10">
      <div className="max-w-xl rounded-3xl border border-destructive/30 bg-destructive/10 p-6 shadow-sm">
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-destructive">Studio Home Unavailable</div>
        <p className="mt-3 text-sm leading-6 text-foreground">{message}</p>
      </div>
    </div>
  )
}


export default function StudioHomePage() {
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function loadStudioHome() {
      try {
        const response = await fetch(`${API_BASE}/api/studio/home`)
        if (!response.ok) {
          throw new Error(`Studio Home request failed: ${response.status} ${response.statusText}`)
        }

        const payload = await response.json()
        if (!cancelled) {
          setSummary(payload)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Studio Home could not be loaded.')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    loadStudioHome()
    return () => {
      cancelled = true
    }
  }, [])

  if (loading) {
    return <LoadingState />
  }

  if (error || !summary) {
    return <ErrorState message={error || 'Studio Home returned no summary.'} />
  }

  const app = summary.app || {}
  const ai = summary.ai || {}
  const theme = summary.theme || {}
  const admin = summary.admin || {}
  const workspace = summary.workspace || {}
  const shell = summary.shell || {}
  const studio = summary.studio || {}
  const home = summary.home || {}

  const journeyLabel = app.journey === 'existing_app' ? 'Existing App' : app.journey === 'new_app' ? 'New App' : 'Not Configured'
  const readinessTone = workspace.runtime_readiness === 'entry_point_configured'
    ? 'success'
    : workspace.runtime_readiness === 'no_workflows'
      ? 'warning'
      : 'primary'

  return (
    <BuilderWorkspaceLayout>
      <div className="flex flex-col gap-6">
        <SurfaceCard title={app.name || 'Studio'} eyebrow="Studio Home" accent className="overflow-hidden">
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
                <Metric label="Provider" value={ai.provider || 'Not configured'} detail={ai.model || 'Choose a default model'} />
                <Metric label="Pages" value={workspace.page_count ?? 0} detail={`${workspace.schema_page_count ?? 0} schema + ${workspace.extension_page_count ?? 0} extension`} />
                <Metric label="Workflows" value={workspace.workflow_count ?? 0} detail={workspace.entry_point || 'No entry point yet'} />
              </div>
            </div>

            <div className="rounded-3xl border border-border bg-background/75 p-5">
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Next Step</div>
              <p className="mt-4 text-sm leading-7 text-foreground">{home.next_step}</p>
              <div className="mt-5 space-y-3 text-sm text-muted-foreground">
                <div>
                  <span className="font-semibold text-foreground">Route:</span> {studio.route || '/studio'}
                </div>
                <div>
                  <span className="font-semibold text-foreground">Workspace:</span> {studio.workspace_root || 'unknown'}
                </div>
                <div>
                  <span className="font-semibold text-foreground">API billed:</span> {ai.api_billed ? 'yes' : 'no'}
                </div>
              </div>
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

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <SurfaceCard title="Workspace Snapshot" eyebrow="Current Shape">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <Metric label="Header Pages" value={shell.header_page_count ?? 0} detail="Shell-owned navigation pills" />
              <Metric label="Header Actions" value={shell.header_action_count ?? 0} detail="Prompted by shell.json" />
              <Metric label="Theme" value={theme.primary || 'Not set'} detail={theme.logo_alt || 'Header logo not configured'} />
            </div>
            {app.first_goal ? (
              <div className="mt-5 rounded-2xl border border-border bg-muted/40 p-4">
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">First Goal</div>
                <p className="mt-2 text-sm leading-7 text-foreground">{app.first_goal}</p>
              </div>
            ) : null}
          </SurfaceCard>

          <SurfaceCard title="Admin And Adoption" eyebrow="Guardrails">
            <div className="space-y-4 text-sm leading-7 text-muted-foreground">
              <div>
                <span className="font-semibold text-foreground">Admin:</span>{' '}
                {admin.enabled ? (admin.admin_emails?.length ? admin.admin_emails.join(', ') : 'enabled without emails') : 'not enabled'}
              </div>
              {app.existing_app_url ? (
                <div>
                  <span className="font-semibold text-foreground">Existing App:</span>{' '}
                  <a className="text-primary underline-offset-4 hover:underline" href={app.existing_app_url} target="_blank" rel="noreferrer">
                    {app.existing_app_url}
                  </a>
                </div>
              ) : null}
              {app.host_owned_summary ? (
                <div>
                  <span className="font-semibold text-foreground">Host-Owned:</span> {app.host_owned_summary}
                </div>
              ) : (
                <div>
                  <span className="font-semibold text-foreground">Host-Owned:</span> Capture what should remain outside Mozaiks before broader generation.
                </div>
              )}
            </div>
          </SurfaceCard>
        </div>
      </div>
    </BuilderWorkspaceLayout>
  )
}
