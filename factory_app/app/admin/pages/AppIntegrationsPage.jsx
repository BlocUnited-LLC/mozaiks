/**
 * AppIntegrationsPage — app-specific integration setup detail.
 *
 * Shows what the last build declared this app needs, cross-referenced with
 * live workspace integration status. Workspace-level credentials are managed
 * once at /integrations. This route is a deep detail page linked from the app
 * overview, not a primary app dashboard section.
 *
 * Custom (uncatalogued) services that have no workspace catalog entry are
 * shown separately with their build-time connector status.
 */
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  API_BASE,
  LinkButton,
  Panel,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  SurfaceCard,
} from '../../ui/components/StudioShared.jsx'

// ── helpers ──────────────────────────────────────────────────────────────────

function humanize(value, fallback = 'Unknown') {
  const s = String(value || '').trim()
  return s ? s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : fallback
}

function workspaceTone(status) {
  if (status === 'configured') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'missing') return 'destructive'
  return 'default'
}

function workspaceLabel(status) {
  const labels = {
    configured: 'Workspace ready',
    partial: 'Partial setup',
    missing: 'Not configured',
  }
  return labels[status] || 'Uncatalogued'
}

function connectorTone(status) {
  if (status === 'ready') return 'success'
  if (status === 'not_configured') return 'warning'
  return 'default'
}

function connectorLabel(status) {
  if (status === 'ready') return 'Credential saved'
  if (status === 'not_configured') return 'No credential'
  return humanize(status)
}

// ── DeclaredServiceCard ───────────────────────────────────────────────────────

function CatalogServiceCard({ decl }) {
  const isMissing = decl.workspace_status === 'missing'
  const isPartial = decl.workspace_status === 'partial'
  const needsAttention = isMissing || isPartial

  return (
    <SurfaceCard
      title={decl.display_name || humanize(decl.service)}
      eyebrow={decl.optional ? 'Optional' : 'Required'}
    >
      <div className="flex flex-wrap gap-2">
        <StatusPill tone={workspaceTone(decl.workspace_status)}>
          {workspaceLabel(decl.workspace_status)}
        </StatusPill>
      </div>

      {decl.purpose && (
        <p className="mt-2 text-sm leading-6 text-muted-foreground">{decl.purpose}</p>
      )}

      {needsAttention && (
        <div className="mt-3">
          <LinkButton to="/integrations" size="sm">
            {isMissing ? 'Configure in workspace →' : 'Complete setup in workspace →'}
          </LinkButton>
        </div>
      )}

      {decl.required_at && (
        <p className="mt-2 text-xs text-muted-foreground/70">
          Needed at {humanize(decl.required_at, 'runtime')}
        </p>
      )}
    </SurfaceCard>
  )
}

function CustomServiceCard({ decl }) {
  return (
    <SurfaceCard
      title={decl.display_name || humanize(decl.service)}
      eyebrow={decl.optional ? 'Optional · Custom' : 'Required · Custom'}
    >
      <div className="flex flex-wrap gap-2">
        <StatusPill tone={connectorTone(decl.connector_status)}>
          {connectorLabel(decl.connector_status)}
        </StatusPill>
      </div>

      {decl.purpose && (
        <p className="mt-2 text-sm leading-6 text-muted-foreground">{decl.purpose}</p>
      )}

      <p className="mt-2 text-xs text-muted-foreground/70">
        Not in workspace catalog — configure credentials directly in your environment.
      </p>
    </SurfaceCard>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

export default function AppIntegrationsPage() {
  const { appId = 'workspace-app' } = useParams()
  const navigate = useNavigate()
  const [declarations, setDeclarations] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const res = await fetch(
          `${API_BASE}/api/modules/workspace_integrations/list_app_integration_needs`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ app_id: appId }),
          },
        )
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) {
          setDeclarations(payload)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load integrations.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [appId])

  if (loading) return <StudioLoadingState label="Loading Integrations…" />
  if (error) return <StudioErrorState title="Integrations Unavailable" message={error} />

  const declList = Array.isArray(declarations?.declarations) ? declarations.declarations : []
  const summary = declarations?.summary || {}

  const catalogServices = declList.filter((d) => Boolean(d.catalog_id))
  const customServices = declList.filter((d) => !d.catalog_id)

  const workspaceReadyCount = catalogServices.filter((d) => d.workspace_status === 'configured').length
  const workspaceMissingCount = catalogServices.filter(
    (d) => d.workspace_status === 'missing' || d.workspace_status === 'partial',
  ).length

  const summaryItems = [
    {
      id: 'declared',
      label: 'App services',
      value: declList.length,
      detail: declList.length === 0 ? 'Run a build to populate' : `${summary.required ?? 0} required`,
    },
    {
      id: 'workspace_ready',
      label: 'Ready globally',
      value: workspaceReadyCount,
      detail: 'Configured in workspace',
    },
    {
      id: 'needs_setup',
      label: 'Needs setup',
      value: workspaceMissingCount,
      detail: summary.blocking > 0 ? `${summary.blocking} blocking` : 'Configure at /integrations',
    },
    {
      id: 'custom',
      label: 'Custom',
      value: customServices.length,
      detail: 'Not in workspace catalog',
    },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Integration Setup"
          subtitle="App-specific service requirements from the latest build. Shared provider credentials and operator notes are managed once at the workspace level."
          actions={[{ id: 'workspace-integrations', label: 'Workspace integrations', variant: 'outline' }]}
          onAction={(id) => {
            if (id === 'workspace-integrations') navigate('/integrations')
          }}
        />

        <SummaryStrip items={summaryItems} />

        {declList.length === 0 ? (
          <StudioInlineEmptyState
            title="No app-specific services declared"
            description="Run a build to see which third-party services this app needs. Workspace-level provider setup is still managed globally."
            action={<LinkButton to="/integrations" variant="secondary">Open workspace integrations</LinkButton>}
          />
        ) : (
          <div className="space-y-6">
            {catalogServices.length > 0 && (
              <Panel
                eyebrow="Workspace services"
                title="Workspace-managed services"
                subtitle="These providers are configured once globally. This app only declares that it needs them."
              >
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {catalogServices.map((decl) => (
                    <CatalogServiceCard key={decl.service} decl={decl} />
                  ))}
                </div>
              </Panel>
            )}

            {customServices.length > 0 && (
              <Panel
                eyebrow="Custom services"
                title="App-specific services"
                subtitle="These services are not in the workspace catalog. Configure their credentials directly in this app's environment."
              >
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {customServices.map((decl) => (
                    <CustomServiceCard key={decl.service} decl={decl} />
                  ))}
                </div>
              </Panel>
            )}
          </div>
        )}
      </div>
    </WorkspaceLayout>
  )
}
