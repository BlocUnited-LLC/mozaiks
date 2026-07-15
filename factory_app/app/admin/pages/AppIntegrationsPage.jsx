/**
 * AppIntegrationsPage — app-specific service requirements.
 *
 * Shows what the latest build says this app needs. Workspace-level credentials
 * are configured once at /integrations; this page only explains the app's
 * requirements and optional services.
 */
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  API_BASE,
  LinkButton,
  Panel,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'

// ── helpers ──────────────────────────────────────────────────────────────────

function humanize(value, fallback = 'Unknown') {
  const s = String(value || '').trim()
  return s ? s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : fallback
}

function isCatalogReady(decl) {
  return Boolean(decl?.catalog_id) && decl?.workspace_status === 'configured'
}

function isCustomReady(decl) {
  return !decl?.catalog_id && decl?.connector_status === 'ready'
}

function isReady(decl) {
  return isCatalogReady(decl) || isCustomReady(decl)
}

function needsSetup(decl) {
  if (isReady(decl)) return false
  if (decl?.catalog_id) return decl.workspace_status === 'missing' || decl.workspace_status === 'partial'
  return decl?.connector_status === 'not_configured' || decl?.connector_status === 'partial'
}

function serviceTone(decl) {
  if (isReady(decl)) return 'success'
  if (needsSetup(decl)) return 'warning'
  return 'default'
}

function serviceLabel(decl) {
  if (isReady(decl)) return 'Ready'
  if (decl?.workspace_status === 'partial' || decl?.connector_status === 'partial') return 'Partial setup'
  if (needsSetup(decl)) return 'Needs setup'
  return 'Review'
}

function requiredAtLabel(value) {
  const label = humanize(value, 'Runtime')
  return `Needed at ${label.toLowerCase()}`
}

async function fetchAppIntegrationDeclarations(appId) {
  const res = await fetch(
    `${API_BASE}/api/modules/workspace_integrations/list_app_integration_needs`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app_id: appId }),
    },
  )
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function deleteAppIntegrationNeed(appId, service) {
  const res = await fetch(`${API_BASE}/api/modules/workspace_integrations/delete_app_integration_need`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app_id: appId, service }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// ── rows ─────────────────────────────────────────────────────────────────────

function ServiceRow({ decl, onRemove, removing }) {
  const setupNeeded = needsSetup(decl)
  const isCustom = !decl.catalog_id

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border/55 bg-card/38 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold text-foreground">
            {decl.display_name || humanize(decl.service)}
          </h3>
          <StatusPill tone={serviceTone(decl)}>{serviceLabel(decl)}</StatusPill>
          <StatusPill tone="muted">{decl.optional ? 'Optional' : 'Required'}</StatusPill>
          {isCustom && <StatusPill tone="muted">App-specific</StatusPill>}
        </div>

        {decl.purpose && (
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{decl.purpose}</p>
        )}
        <div className="mt-2 text-xs text-muted-foreground/70">
          {requiredAtLabel(decl.required_at)}
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {setupNeeded && decl.catalog_id && (
          <LinkButton to="/integrations" size="sm">
            Connect in workspace
          </LinkButton>
        )}
        {setupNeeded && isCustom && (
          <StatusPill tone="warning">Configure in app environment</StatusPill>
        )}
        {decl.removable && (
          <ActionButton
            variant="secondary"
            size="sm"
            onClick={() => onRemove(decl)}
            disabled={removing}
          >
            {removing ? 'Removing...' : 'Remove from app'}
          </ActionButton>
        )}
      </div>
    </div>
  )
}

function ServiceSection({ title, subtitle, items, onRemove, removingService, empty }) {
  if (!items.length) return empty || null
  return (
    <Panel title={title} subtitle={subtitle}>
      <div className="space-y-3">
        {items.map((decl) => (
          <ServiceRow
            key={decl.service}
            decl={decl}
            onRemove={onRemove}
            removing={removingService === decl.service}
          />
        ))}
      </div>
    </Panel>
  )
}

// ── page ─────────────────────────────────────────────────────────────────────

export default function AppIntegrationsPage() {
  const { appId = 'workspace-app' } = useParams()
  const navigate = useNavigate()
  const [declarations, setDeclarations] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [removingService, setRemovingService] = useState(null)

  async function load() {
    const payload = await fetchAppIntegrationDeclarations(appId)
    setDeclarations(payload)
  }

  useEffect(() => {
    let cancelled = false

    async function loadForEffect() {
      try {
        const payload = await fetchAppIntegrationDeclarations(appId)
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

    loadForEffect()
    return () => { cancelled = true }
  }, [appId])

  async function handleRemoveDeclaration(decl) {
    const service = String(decl?.service || '').trim()
    if (!service) return
    const label = decl.display_name || humanize(service)
    if (!window.confirm(`Remove ${label} from this app?`)) return

    setRemovingService(service)
    setActionError(null)
    try {
      await deleteAppIntegrationNeed(appId, service)
      await load()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Integration could not be removed.')
    } finally {
      setRemovingService(null)
    }
  }

  if (loading) return <StudioLoadingState label="Loading integrations..." />
  if (error) return <StudioErrorState title="Integrations Unavailable" message={error} />

  const declList = Array.isArray(declarations?.declarations) ? declarations.declarations : []
  const catalogServices = declList.filter((d) => Boolean(d.catalog_id))
  const customServices = declList.filter((d) => !d.catalog_id)
  const requiredServices = catalogServices.filter((d) => !d.optional)
  const optionalServices = catalogServices.filter((d) => d.optional)
  const readyCount = declList.filter(isReady).length
  const setupCount = declList.filter(needsSetup).length

  const summaryItems = [
    {
      id: 'ready',
      label: 'Ready',
      value: readyCount,
      detail: 'Usable by this app',
    },
    {
      id: 'setup',
      label: 'Needs setup',
      value: setupCount,
      detail: setupCount > 0 ? 'Action required' : 'No blockers',
    },
    {
      id: 'optional',
      label: 'Optional',
      value: declList.filter((d) => d.optional).length,
      detail: 'Can be removed',
    },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="App Integrations"
          subtitle="Services this app needs. Workspace connections are configured once and reused by apps that declare them."
          actions={[{ id: 'workspace-integrations', label: 'Workspace integrations', variant: 'outline' }]}
          onAction={(id) => {
            if (id === 'workspace-integrations') navigate('/integrations')
          }}
        />

        <SummaryStrip items={summaryItems} />

        {actionError && (
          <div className="rounded-md border border-destructive/35 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {actionError}
          </div>
        )}

        {declList.length === 0 ? (
          <StudioInlineEmptyState
            title="No integrations declared"
            description="Once a build identifies required services, they appear here. Workspace-level setup is still managed globally."
            action={<LinkButton to="/integrations" variant="secondary">Open workspace integrations</LinkButton>}
          />
        ) : (
          <div className="space-y-5">
            <ServiceSection
              title="Required"
              subtitle="Services this app expects before its related features are fully usable."
              items={requiredServices}
              onRemove={handleRemoveDeclaration}
              removingService={removingService}
              empty={null}
            />
            <ServiceSection
              title="Optional"
              subtitle="Enhancements or defaults that can be removed from this app."
              items={optionalServices}
              onRemove={handleRemoveDeclaration}
              removingService={removingService}
              empty={null}
            />
            <ServiceSection
              title="App-specific"
              subtitle="Services not in the workspace catalog. These are configured in the app environment."
              items={customServices}
              onRemove={handleRemoveDeclaration}
              removingService={removingService}
              empty={null}
            />
          </div>
        )}
      </div>
    </WorkspaceLayout>
  )
}
