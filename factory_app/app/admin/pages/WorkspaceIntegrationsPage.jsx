import { useEffect, useState } from 'react'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  API_BASE,
  Panel,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  StudioSlideOver,
} from '../../ui/components/StudioShared.jsx'

// ── helpers ──────────────────────────────────────────────────────────────────

function statusTone(status) {
  if (status === 'configured') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'missing') return 'destructive'
  return 'default'
}

function statusLabel(status, item = null) {
  const resolvedStatus = item?.display_status || status
  if (resolvedStatus === 'configured') return 'Connected'
  if (resolvedStatus === 'partial') return 'Needs setup'
  if (resolvedStatus === 'missing' && Number(item?.app_usage_count || 0) > 0) return 'Needs setup'
  if (resolvedStatus === 'missing') return 'Available'
  return 'Available'
}

function displayTone(item) {
  const resolvedStatus = item?.display_status || item?.status
  if (resolvedStatus === 'missing' && Number(item?.app_usage_count || 0) === 0) return 'default'
  return statusTone(resolvedStatus)
}

function withConnectorOverlay(item, connector) {
  if (connector?.ready) {
    return { ...item, display_status: 'configured', workspace_connector_status: 'ready' }
  }
  if (connector) {
    return { ...item, workspace_connector_status: 'partial' }
  }
  return item
}

function effectiveStatus(item) {
  return item?.display_status || item?.status
}

function humanize(key) {
  return String(key || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function appUsageLabel(item) {
  const count = Number(item?.app_usage_count || 0)
  if (count === 1) return 'Used by 1 app'
  if (count > 1) return `Used by ${count} apps`
  return 'Not used yet'
}

function needsAttention(item) {
  const usedByApps = Number(item?.app_usage_count || 0) > 0
  return effectiveStatus(item) === 'partial' || (effectiveStatus(item) === 'missing' && usedByApps)
}

function connectorLabel(connector, item) {
  if (connector) return 'Workspace connector'
  if (item?.status === 'configured') return 'Environment variables'
  return 'No saved connector'
}

function connectorDescription(connector, item) {
  if (connector?.ready) {
    return 'A workspace connector is saved for this service. Apps that declare this service can reuse it without collecting credentials again.'
  }
  if (connector) {
    return 'A workspace connector exists, but it still needs required configuration before apps can use it.'
  }
  if (item?.status === 'configured') {
    return 'This service is configured by backend environment variables. Rotate or remove those values in the environment that starts the backend.'
  }
  if (Number(item?.app_usage_count || 0) > 0) {
    return 'At least one app needs this service, but the workspace does not have a complete connector or environment setup yet.'
  }
  return 'This service is available for future apps. No app currently depends on it.'
}

function actionLabel(item, connector) {
  if (needsAttention(item)) return 'Review setup'
  if (connector || effectiveStatus(item) === 'configured') return 'Manage'
  return 'Review'
}

// ── API ───────────────────────────────────────────────────────────────────────

async function fetchIntegrations() {
  const res = await fetch(`${API_BASE}/api/modules/workspace_integrations/list_integrations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function fetchWorkspaceConnectors() {
  const res = await fetch(`${API_BASE}/api/modules/workspace_integrations/list_workspace_connectors`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function saveNote(integrationId, note) {
  const res = await fetch(`${API_BASE}/api/modules/workspace_integrations/set_integration_note`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ integration_id: integrationId, note }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function deleteWorkspaceConnector(service) {
  const res = await fetch(`${API_BASE}/api/modules/workspace_integrations/delete_workspace_connector`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ service }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// ── SetupSlideOver ────────────────────────────────────────────────────────────

function SetupSlideOver({
  open,
  item,
  connector,
  connectorError,
  onClose,
  onSave,
  onDelete,
  saving,
  deleting,
  actionError,
}) {
  const [note, setNote] = useState('')
  const [copied, setCopied] = useState(null)

  useEffect(() => {
    if (open) setNote(item?.note || '')
  }, [open, item])

  function copyToClipboard(text) {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(text)
      setTimeout(() => setCopied(null), 1500)
    })
  }

  if (!item) return null

  const secrets = Array.isArray(item.secrets) ? item.secrets : []
  const steps = Array.isArray(item.setup_steps) ? item.setup_steps : []
  const missingSecrets = secrets.filter((secret) => !secret.present)
  const hasStoredConnector = Boolean(connector?.service)

  const footer = (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        {hasStoredConnector ? (
          <ActionButton
            variant="destructive"
            onClick={() => onDelete(item.id)}
            disabled={saving || deleting}
          >
            {deleting ? 'Deleting...' : 'Delete connector'}
          </ActionButton>
        ) : (
          <p className="max-w-sm text-xs leading-5 text-muted-foreground">
            Delete appears only for saved workspace connectors.
          </p>
        )}
      </div>
      <div className="flex justify-end gap-3">
        <ActionButton variant="secondary" onClick={onClose} disabled={saving || deleting}>
          Close
        </ActionButton>
        <ActionButton onClick={() => onSave(item.id, note)} disabled={saving || deleting}>
          {saving ? 'Saving...' : 'Save note'}
        </ActionButton>
      </div>
    </div>
  )

  return (
    <StudioSlideOver
      open={open}
      title={item.name}
      description={item.description || `${humanize(item.category)} integration`}
      onClose={onClose}
      footer={footer}
      backdrop="dim"
      maxWidthClass="max-w-2xl"
    >
      <div className="space-y-5">
        {actionError && (
          <div className="rounded-md border border-destructive/35 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {actionError}
          </div>
        )}

        <div className="rounded-lg border border-border/55 bg-background/35 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={displayTone(item)}>
              {statusLabel(item.status, item)}
            </StatusPill>
            <StatusPill tone="muted">{appUsageLabel(item)}</StatusPill>
          </div>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            {connectorDescription(connector, item)}
          </p>
          {connectorError && (
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              Saved connector inventory could not be loaded: {connectorError}
            </p>
          )}
        </div>

        <div className="rounded-lg border border-border/55 bg-background/35 p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Credential source
          </div>
          <div className="mt-1 text-sm font-medium text-foreground">
            {connectorLabel(connector, item)}
          </div>
          {connector?.health?.message && (
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{connector.health.message}</p>
          )}
        </div>

        <div>
          <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Workspace note
          </label>
          <textarea
            className="mt-2 min-h-24 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/50"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Owner, rotation schedule, environment scope..."
          />
        </div>

        <details className="rounded-lg border border-border/55 bg-background/25 p-4">
          <summary className="cursor-pointer text-sm font-semibold text-foreground">
            Advanced setup details
          </summary>
          <div className="mt-4 space-y-5">
            {missingSecrets.length > 0 && (
              <div className="rounded-md border border-warning/35 bg-warning/10 px-3 py-2 text-sm text-warning">
                Missing {missingSecrets.length} required setting{missingSecrets.length === 1 ? '' : 's'}.
              </div>
            )}

            {secrets.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Required settings
                </div>
                <div className="space-y-2">
                  {secrets.map((s) => (
                    <div
                      key={s.name}
                      className="flex items-center justify-between gap-3 rounded-md border border-border bg-background/60 px-3 py-2"
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <StatusPill tone={s.present ? 'success' : 'destructive'}>
                          {s.present ? 'Set' : 'Missing'}
                        </StatusPill>
                        <code className="truncate font-mono text-sm text-foreground">{s.name}</code>
                      </div>
                      <ActionButton
                        variant="secondary"
                        size="sm"
                        onClick={() => copyToClipboard(s.name)}
                      >
                        {copied === s.name ? 'Copied' : 'Copy'}
                      </ActionButton>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {steps.length > 0 && (
              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Setup steps
                </div>
                <ol className="space-y-2">
                  {steps.map((step, i) => (
                    <li key={i} className="flex gap-3 text-sm leading-6 text-muted-foreground">
                      <span className="flex-none text-xs font-semibold tabular-nums text-muted-foreground/60 pt-0.5">
                        {i + 1}.
                      </span>
                      <span>{step}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        </details>
      </div>
    </StudioSlideOver>
  )
}

// ── IntegrationRow ───────────────────────────────────────────────────────────

function IntegrationRow({ item, connector, onOpen }) {
  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border/55 bg-card/38 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold text-foreground">{item.name}</h3>
          <StatusPill tone={displayTone(item)}>{statusLabel(item.status, item)}</StatusPill>
          <StatusPill tone="muted">{humanize(item.category)}</StatusPill>
        </div>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">{item.description}</p>
        <div className="mt-2 text-xs text-muted-foreground/70">{appUsageLabel(item)}</div>
      </div>
      <ActionButton variant="secondary" onClick={() => onOpen(item)} className="shrink-0">
        {actionLabel(item, connector)}
      </ActionButton>
    </div>
  )
}

function IntegrationSection({ title, subtitle, items, connectorsByService, onOpen, empty = null }) {
  if (!items.length) return empty
  return (
    <Panel title={title} subtitle={subtitle}>
      <div className="space-y-3">
        {items.map((item) => (
          <IntegrationRow
            key={item.id}
            item={item}
            connector={connectorsByService.get(item.id)}
            onOpen={onOpen}
          />
        ))}
      </div>
    </Panel>
  )
}

// ── page ──────────────────────────────────────────────────────────────────────

export default function WorkspaceIntegrationsPage() {
  const [data, setData] = useState(null)
  const [connectors, setConnectors] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [connectorError, setConnectorError] = useState(null)
  const [activeItem, setActiveItem] = useState(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [actionError, setActionError] = useState(null)

  async function load() {
    setLoading(true)
    try {
      const payload = await fetchIntegrations()
      setData(payload)
      setError(null)
      try {
        const connectorPayload = await fetchWorkspaceConnectors()
        setConnectors(Array.isArray(connectorPayload.connectors) ? connectorPayload.connectors : [])
        setConnectorError(null)
      } catch (connectorErr) {
        setConnectors([])
        setConnectorError(connectorErr instanceof Error ? connectorErr.message : 'Connector inventory unavailable.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load integrations.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleSaveNote(integrationId, note) {
    setSaving(true)
    setActionError(null)
    try {
      await saveNote(integrationId, note)
      setActiveItem(null)
      await load()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Note could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDeleteConnector(service) {
    const connector = connectors.find((candidate) => candidate?.service === service)
    if (!connector) return
    const label = connector.display_name || activeItem?.name || humanize(service)
    if (!window.confirm(`Delete the saved workspace connector for ${label}?`)) return

    setDeleting(true)
    setActionError(null)
    try {
      await deleteWorkspaceConnector(service)
      setActiveItem(null)
      await load()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Connector could not be deleted.')
    } finally {
      setDeleting(false)
    }
  }

  function closeSlideOver() {
    setActiveItem(null)
    setActionError(null)
  }

  if (loading) return <StudioLoadingState label="Loading integrations..." />
  if (error || !data) return <StudioErrorState title="Integrations Unavailable" message={error || 'No data returned.'} />

  const rawIntegrations = Array.isArray(data.integrations) ? data.integrations : []
  const summary = data.summary || {}
  const connectorsByService = new Map(
    connectors
      .filter((connector) => connector?.service)
      .map((connector) => [connector.service, connector]),
  )
  const integrations = rawIntegrations.map((item) => withConnectorOverlay(item, connectorsByService.get(item.id)))
  const activeConnector = activeItem ? connectorsByService.get(activeItem.id) : null

  const attentionItems = integrations.filter(needsAttention)
  const connectedItems = integrations.filter((item) => effectiveStatus(item) === 'configured' && !needsAttention(item))
  const availableItems = integrations.filter((item) => !needsAttention(item) && effectiveStatus(item) !== 'configured')
  const usedCount = integrations.filter((item) => Number(item.app_usage_count || 0) > 0).length

  const summaryItems = [
    {
      id: 'connected',
      label: 'Connected',
      value: connectedItems.length,
      detail: 'Ready for apps',
    },
    {
      id: 'attention',
      label: 'Needs setup',
      value: attentionItems.length,
      detail: attentionItems.length > 0 ? 'Used by apps' : 'No blockers',
    },
    {
      id: 'used',
      label: 'Used by apps',
      value: summary.used ?? usedCount,
      detail: 'Declared by builds',
    },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Integrations"
          subtitle="Connect shared services once. Apps declare what they need, and this page shows only the setup that matters."
        />

        <SummaryStrip items={summaryItems} />

        {integrations.length === 0 ? (
          <StudioInlineEmptyState
            title="No integrations available"
            description="The workspace integration catalog is empty."
          />
        ) : (
          <div className="space-y-5">
            <IntegrationSection
              title="Needs attention"
              subtitle="Services currently used by apps but missing required setup."
              items={attentionItems}
              connectorsByService={connectorsByService}
              onOpen={setActiveItem}
              empty={null}
            />
            <IntegrationSection
              title="Connected"
              subtitle="Services that are ready for apps to reuse."
              items={connectedItems}
              connectorsByService={connectorsByService}
              onOpen={setActiveItem}
            />
            <IntegrationSection
              title="Available"
              subtitle="Supported services that are not blocking any app right now."
              items={availableItems}
              connectorsByService={connectorsByService}
              onOpen={setActiveItem}
            />
          </div>
        )}

        <SetupSlideOver
          open={Boolean(activeItem)}
          item={activeItem}
          connector={activeConnector}
          connectorError={connectorError}
          onClose={closeSlideOver}
          onSave={handleSaveNote}
          onDelete={handleDeleteConnector}
          saving={saving}
          deleting={deleting}
          actionError={actionError}
        />
      </div>
    </WorkspaceLayout>
  )
}
