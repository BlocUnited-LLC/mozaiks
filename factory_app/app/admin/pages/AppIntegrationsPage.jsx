import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  API_BASE,
  StudioInlineEmptyState,
  StatusPill,
  Panel,
  SurfaceCard,
  StudioSlideOver,
  StudioLoadingState,
  StudioErrorState,
} from '../../ui/components/StudioShared.jsx'


const EMPTY_FORM_STATE = {
  service: '',
  displayName: '',
  secretValue: '',
  notes: '',
  ttlDays: '30',
}

function humanize(value, fallback = 'not set') {
  const normalized = String(value || '').trim()
  return normalized ? normalized.replaceAll('_', ' ') : fallback
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return 'Not set'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function formatDateTime(value) {
  if (!value) return null
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value))
  } catch {
    return value
  }
}

function isSecretFieldName(name) {
  return /secret|api[_-]?key|apikey|token|password/i.test(String(name || ''))
}

function getConnectorHealth(connector) {
  const status = connector?.health?.status || (connector?.secret_available ? 'configured' : 'unknown')
  return {
    status,
    message: connector?.health?.message || null,
    missingFields: Array.isArray(connector?.health?.missing_fields) ? connector.health.missing_fields : [],
    lastCheckedAt: connector?.health?.last_checked_at || null,
    checkedBy: connector?.health?.checked_by || null,
    safeDetails: connector?.health?.safe_details && typeof connector.health.safe_details === 'object'
      ? connector.health.safe_details
      : {},
  }
}

function getHealthTone(status) {
  if (status === 'healthy' || status === 'configured') return 'success'
  if (status === 'not_configured') return 'warning'
  if (status === 'unhealthy') return 'destructive'
  return 'default'
}

function getHealthLabel(status) {
  const labels = {
    configured: 'Configured',
    healthy: 'Healthy',
    not_configured: 'Missing setup',
    unhealthy: 'Needs attention',
    unknown: 'Unknown',
  }
  return labels[status] || humanize(status, 'Unknown')
}

function getReadinessTone(connector, health) {
  if (connector.ready || connector.configured || health.status === 'configured' || health.status === 'healthy') return 'success'
  if (health.status === 'unhealthy') return 'destructive'
  if (health.status === 'not_configured') return 'warning'
  return 'default'
}

function getReadinessLabel(connector, health) {
  if (connector.ready || connector.configured || health.status === 'configured' || health.status === 'healthy') return 'Ready'
  if (health.status === 'unhealthy') return 'Review'
  if (health.status === 'not_configured') return 'Incomplete'
  return 'Pending'
}

function getSafePublicConfigEntries(connector) {
  const publicConfig = connector?.public_config
  if (!publicConfig || typeof publicConfig !== 'object' || Array.isArray(publicConfig)) return []
  return Object.entries(publicConfig)
    .filter(([key]) => !isSecretFieldName(key))
    .map(([key, value]) => ({ key, value: formatValue(value) }))
}

function connectorSupportsHealthCheck(connector) {
  return Boolean(connector?.health_check_supported || connector?.health?.health_check_supported)
}

function getSafeHealthDetailEntries(health) {
  const details = health?.safeDetails
  if (!details || typeof details !== 'object' || Array.isArray(details)) return []
  return Object.entries(details)
    .filter(([key]) => !isSecretFieldName(key))
    .map(([key, value]) => ({ key, value: formatValue(value) }))
}

function getRequiredFieldRows(connector, health) {
  const missing = new Set(health.missingFields)
  const requiredFields = Array.isArray(connector?.required_fields) ? connector.required_fields : []
  return requiredFields
    .filter((field) => field?.required !== false)
    .map((field) => {
      const name = field?.name || field?.id || field?.label || 'field'
      const secret = field?.type === 'secret' || field?.frontend_safe === false || isSecretFieldName(name)
      const missingField = missing.has(name)
      return {
        name,
        label: field?.label || humanize(name),
        secret,
        missing: missingField,
      }
    })
}

function SavedSecretPill({ connector }) {
  return (
    <StatusPill tone={connector.secret_available ? 'success' : 'warning'}>
      {connector.secret_available ? 'Credential saved' : 'Credential not saved'}
    </StatusPill>
  )
}

function ConnectorHealthPill({ health }) {
  return (
    <StatusPill tone={getHealthTone(health.status)}>
      {getHealthLabel(health.status)}
    </StatusPill>
  )
}

function ConnectorReadinessPill({ connector, health }) {
  return (
    <StatusPill tone={getReadinessTone(connector, health)}>
      {getReadinessLabel(connector, health)}
    </StatusPill>
  )
}

function ConnectorCard({ connector, onEdit, onHealthCheck, checkingHealth, healthError }) {
  const title = connector.display_name || humanize(connector.service)
  const notePreview = connector.notes?.trim() || null
  const updatedLabel = formatDateTime(connector.updated_at)
  const expiresLabel = formatDateTime(connector.expires_at)
  const health = getConnectorHealth(connector)
  const lastCheckedLabel = formatDateTime(health.lastCheckedAt)
  const fieldRows = getRequiredFieldRows(connector, health)
  const publicConfigEntries = getSafePublicConfigEntries(connector)
  const safeHealthDetails = getSafeHealthDetailEntries(health)
  const supportsHealthCheck = connectorSupportsHealthCheck(connector)
  const missingFields = fieldRows.length > 0
    ? fieldRows.filter((field) => field.missing).map((field) => field.label)
    : health.missingFields.map((field) => humanize(field))

  return (
    <SurfaceCard title={title} eyebrow="Integration">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-wrap gap-2">
          <ConnectorHealthPill health={health} />
          <ConnectorReadinessPill connector={connector} health={health} />
          <SavedSecretPill connector={connector} />
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {supportsHealthCheck && (
            <ActionButton
              variant="secondary"
              disabled={checkingHealth}
              onClick={() => onHealthCheck(connector)}
            >
              {checkingHealth ? 'Checking...' : 'Check now'}
            </ActionButton>
          )}
          <ActionButton variant="secondary" onClick={() => onEdit(connector)}>
            Edit
          </ActionButton>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        {notePreview && <p className="text-sm leading-7 text-muted-foreground">{notePreview}</p>}
        {health.message && <p className="text-sm leading-7 text-muted-foreground">{health.message}</p>}
        {healthError && <p className="text-sm leading-7 text-destructive">{healthError}</p>}
        {missingFields.length > 0 && (
          <div className="rounded-lg border border-warning/25 bg-warning/8 px-3 py-2 text-sm text-warning">
            Missing required fields: {missingFields.join(', ')}
          </div>
        )}
        {fieldRows.length > 0 && (
          <div className="space-y-2 rounded-lg border border-border/60 bg-background/45 px-3 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Required fields</div>
            {fieldRows.map((field) => (
              <div key={field.name} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="text-muted-foreground">{field.label}</span>
                <StatusPill tone={field.missing ? 'warning' : 'success'}>
                  {field.missing ? 'Missing required field' : field.secret ? 'Configured' : 'Present'}
                </StatusPill>
              </div>
            ))}
          </div>
        )}
        {publicConfigEntries.length > 0 && (
          <div className="space-y-2 rounded-lg border border-border/60 bg-background/45 px-3 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Safe configuration</div>
            {publicConfigEntries.map((entry) => (
              <div key={entry.key} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="text-muted-foreground">{humanize(entry.key)}</span>
                <span className="max-w-full break-all text-foreground">{entry.value}</span>
              </div>
            ))}
          </div>
        )}
        {safeHealthDetails.length > 0 && (
          <div className="space-y-2 rounded-lg border border-border/60 bg-background/45 px-3 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Health details</div>
            {safeHealthDetails.map((entry) => (
              <div key={entry.key} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="text-muted-foreground">{humanize(entry.key)}</span>
                <span className="max-w-full break-all text-foreground">{entry.value}</span>
              </div>
            ))}
          </div>
        )}
        <div className="grid gap-1 text-sm text-muted-foreground sm:grid-cols-2">
          {updatedLabel && <span>Updated {updatedLabel}</span>}
          {expiresLabel && <span>Expires {expiresLabel}</span>}
          {lastCheckedLabel && <span>Last checked {lastCheckedLabel}</span>}
          {health.checkedBy && <span>Source {humanize(health.checkedBy)}</span>}
        </div>
      </div>
    </SurfaceCard>
  )
}

function AdapterEditorOverlay({
  open,
  mode,
  draft,
  setDraft,
  vaultConfigured,
  saving,
  deleting,
  onClose,
  onSubmit,
  onDelete,
}) {
  const isEdit = mode === 'edit'
  const title = isEdit ? 'Edit Integration' : 'Add Integration'
  const description = isEdit
    ? 'Update the integration record and replace its saved credential when needed.'
    : 'Register a new external service for this app.'
  const submitLabel = isEdit ? 'Save integration' : 'Add integration'
  const canSubmit = Boolean(draft.service.trim())
  const credentialPlaceholder = vaultConfigured
    ? (isEdit ? 'Leave blank to keep the current credential' : 'Optional: save a reusable key or secret now')
    : 'Turn on credential saving before storing a reusable key or secret'
  const footer = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        {isEdit && (
          <ActionButton variant="destructive" disabled={saving || deleting} onClick={onDelete}>
            {deleting ? 'Deleting…' : 'Delete'}
          </ActionButton>
        )}
      </div>
      <div className="flex flex-wrap gap-3">
        <ActionButton variant="secondary" onClick={onClose} disabled={saving || deleting}>
          Cancel
        </ActionButton>
        <ActionButton onClick={onSubmit} disabled={!canSubmit || saving || deleting}>
          {saving ? 'Saving…' : submitLabel}
        </ActionButton>
      </div>
    </div>
  )

  return (
    <StudioSlideOver open={open} title={title} description={description} onClose={onClose} footer={footer}>
      <div className="space-y-5">
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Service
          </label>
          <input
            className="mt-2 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground disabled:opacity-70"
            value={draft.service}
            onChange={(event) => setDraft((prev) => ({ ...prev, service: event.target.value }))}
            placeholder="analytics_provider"
            disabled={isEdit}
          />
        </div>

        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Display Name
          </label>
          <input
            className="mt-2 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
            value={draft.displayName}
            onChange={(event) => setDraft((prev) => ({ ...prev, displayName: event.target.value }))}
            placeholder="Hosted Analytics"
          />
        </div>

        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            API Key / Secret
          </label>
          <input
            type="password"
            className="mt-2 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground disabled:opacity-60"
            value={draft.secretValue}
            onChange={(event) => setDraft((prev) => ({ ...prev, secretValue: event.target.value }))}
            placeholder={credentialPlaceholder}
            disabled={!vaultConfigured}
          />
        </div>

        {vaultConfigured && (
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Secret TTL (days)
            </label>
            <input
              type="number"
              min="1"
              className="mt-2 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
              value={draft.ttlDays}
              onChange={(event) => setDraft((prev) => ({ ...prev, ttlDays: event.target.value }))}
            />
          </div>
        )}

        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Notes
          </label>
          <textarea
            className="mt-2 min-h-32 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
            value={draft.notes}
            onChange={(event) => setDraft((prev) => ({ ...prev, notes: event.target.value }))}
          />
        </div>
      </div>
    </StudioSlideOver>
  )
}

export default function AppIntegrationsPage() {
  const { appId = 'workspace-app' } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState({})
  const [healthErrors, setHealthErrors] = useState({})
  const [overlayMode, setOverlayMode] = useState(null)
  const [activeService, setActiveService] = useState(null)
  const [draft, setDraft] = useState(EMPTY_FORM_STATE)

  async function load() {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/studio/integrations?app_id=${encodeURIComponent(appId)}`)
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const payload = await res.json()
      setData(payload)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Integrations could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false

    async function run() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/integrations?app_id=${encodeURIComponent(appId)}`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) {
          setData(payload)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Integrations could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    run()
    return () => { cancelled = true }
  }, [appId])

  async function saveConnector(service, body) {
    setBusy((prev) => ({ ...prev, [`save:${service}`]: true }))
    try {
      const res = await fetch(`${API_BASE}/api/studio/integrations/connectors/${encodeURIComponent(service)}?app_id=${encodeURIComponent(appId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      await load()
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Integration update failed.')
      return false
    } finally {
      setBusy((prev) => ({ ...prev, [`save:${service}`]: false }))
    }
  }

  async function deleteConnector(service) {
    setBusy((prev) => ({ ...prev, [`delete:${service}`]: true }))
    try {
      const res = await fetch(`${API_BASE}/api/studio/integrations/connectors/${encodeURIComponent(service)}?app_id=${encodeURIComponent(appId)}`, {
        method: 'DELETE',
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      await load()
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Integration deletion failed.')
      return false
    } finally {
      setBusy((prev) => ({ ...prev, [`delete:${service}`]: false }))
    }
  }

  async function createConnector(body) {
    setBusy((prev) => ({ ...prev, create: true }))
    try {
      const res = await fetch(`${API_BASE}/api/studio/integrations/connectors?app_id=${encodeURIComponent(appId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      await load()
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Integration creation failed.')
      return false
    } finally {
      setBusy((prev) => ({ ...prev, create: false }))
    }
  }

  async function checkConnectorHealth(connector) {
    const service = connector?.service
    if (!service) return
    setBusy((prev) => ({ ...prev, [`health:${service}`]: true }))
    setHealthErrors((prev) => ({ ...prev, [service]: null }))
    try {
      const res = await fetch(`${API_BASE}/api/studio/integrations/connectors/${encodeURIComponent(service)}/health-check?app_id=${encodeURIComponent(appId)}`, {
        method: 'POST',
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const payload = await res.json()
      const health = payload?.health && typeof payload.health === 'object' ? payload.health : null
      if (!health) throw new Error('Health check did not return a health result.')
      setData((prev) => {
        if (!prev || !Array.isArray(prev.app_connectors)) return prev
        return {
          ...prev,
          app_connectors: prev.app_connectors.map((item) => (
            item.service === service
              ? { ...item, health: { ...(item.health || {}), ...health } }
              : item
          )),
        }
      })
    } catch (err) {
      setHealthErrors((prev) => ({
        ...prev,
        [service]: err instanceof Error ? err.message : 'Health check failed.',
      }))
    } finally {
      setBusy((prev) => ({ ...prev, [`health:${service}`]: false }))
    }
  }

  if (loading) return <StudioLoadingState label="Loading Integrations…" />
  if (error || !data) return <StudioErrorState title="Integrations Unavailable" message={error || 'No integration data returned.'} />

  const vaultAdapter = data?.runtime_integrations?.connector_vault || data?.integrations?.connector_vault || null
  const connectors = Array.isArray(data.app_connectors) ? data.app_connectors : []
  const connectorSummary = data.connector_summary || {}
  const vaultConfigured = Boolean(vaultAdapter?.configured)
  const savedKeysCount = connectors.filter((connector) => Boolean(connector.secret_available)).length
  const configuredCount = connectorSummary.configured ?? connectors.filter((connector) => {
    const health = getConnectorHealth(connector)
    return health.status === 'configured' || health.status === 'healthy' || connector.configured || connector.ready
  }).length
  const healthyCount = connectorSummary.healthy ?? connectors.filter((connector) => getConnectorHealth(connector).status === 'healthy').length
  const missingCount = connectorSummary.not_configured ?? connectors.filter((connector) => getConnectorHealth(connector).status === 'not_configured').length
  const attentionCount = connectorSummary.unhealthy ?? connectors.filter((connector) => getConnectorHealth(connector).status === 'unhealthy').length
  const unknownCount = connectorSummary.unknown_health ?? connectors.filter((connector) => getConnectorHealth(connector).status === 'unknown').length
  const editingConnector = connectors.find((connector) => connector.service === activeService) || null
  const integrationScopeLabel = 'External Integrations'
  const summaryItems = [
    {
      id: 'integrations',
      label: integrationScopeLabel,
      value: connectorSummary.total || connectors.length,
      detail: 'App-level external services',
    },
    {
      id: 'configured',
      label: 'Configured',
      value: configuredCount,
      detail: `${savedKeysCount} credential${savedKeysCount === 1 ? '' : 's'} saved`,
    },
    {
      id: 'healthy',
      label: 'Healthy',
      value: healthyCount,
      detail: 'Provider checks that passed',
    },
    {
      id: 'missing',
      label: 'Missing required fields',
      value: missingCount,
      detail: 'Incomplete setup',
    },
    {
      id: 'attention',
      label: 'Needs attention',
      value: attentionCount,
      detail: 'Failed health checks',
    },
    {
      id: 'unknown',
      label: 'Unknown',
      value: unknownCount,
      detail: 'Not checked yet',
    },
    {
      id: 'vault',
      label: 'Credential Vault',
      value: vaultConfigured ? 'Ready' : 'Pending',
      detail: vaultConfigured ? 'Reusable app secrets enabled' : 'Credentials cannot be saved yet',
    },
    {
      id: 'workflow',
      label: 'Workflow Reach',
      value: connectors.length > 0 ? 'Connected' : 'Pending',
      detail: 'Expose services to agents and workflows',
    },
  ]

  function closeOverlay() {
    setOverlayMode(null)
    setActiveService(null)
    setDraft(EMPTY_FORM_STATE)
  }

  function openCreateOverlay() {
    setDraft(EMPTY_FORM_STATE)
    setActiveService(null)
    setOverlayMode('create')
  }

  function openEditOverlay(connector) {
    setDraft({
      service: connector.service || '',
      displayName: connector.display_name || connector.service || '',
      secretValue: '',
      notes: connector.notes || '',
      ttlDays: '30',
    })
    setActiveService(connector.service)
    setOverlayMode('edit')
  }

  async function handleOverlaySubmit() {
    if (overlayMode === 'edit' && editingConnector) {
      const saved = await saveConnector(editingConnector.service, {
        display_name: draft.displayName.trim() || null,
        notes: draft.notes.trim() || null,
        secret_value: vaultConfigured ? (draft.secretValue.trim() || null) : null,
        ttl_days: vaultConfigured ? (Number.parseInt(draft.ttlDays, 10) || 30) : null,
      })
      if (saved) closeOverlay()
      return
    }

    if (overlayMode === 'create') {
      const created = await createConnector({
        service: draft.service.trim().toLowerCase(),
        display_name: draft.displayName.trim() || null,
        notes: draft.notes.trim() || null,
        secret_value: vaultConfigured ? (draft.secretValue.trim() || null) : null,
        ttl_days: vaultConfigured ? (Number.parseInt(draft.ttlDays, 10) || 30) : null,
      })
      if (created) closeOverlay()
    }
  }

  async function handleOverlayDelete() {
    if (!editingConnector) return
    const deleted = await deleteConnector(editingConnector.service)
    if (deleted) closeOverlay()
  }

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Integrations"
          subtitle="Keep external services visible at the app boundary so operators know which credentials, dependencies, and workflow touchpoints are in play."
          actions={[
            { id: 'add', label: 'Add Integration', variant: 'primary' },
          ]}
          onAction={openCreateOverlay}
        />

        <SummaryStrip items={summaryItems} />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
          <Panel
            eyebrow="Registry"
            title="Enabled and disabled integrations"
            subtitle="Track which services are registered for this app and whether their reusable credentials are already stored."
          >
            {connectors.length === 0 ? (
              <StudioInlineEmptyState
                title="No integrations registered yet"
                description="Add an email provider, analytics service, CRM, storage provider, or another external service when this app depends on it."
              />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                {connectors.map((connector) => (
                  <ConnectorCard
                    key={connector.service}
                    connector={connector}
                    onEdit={openEditOverlay}
                    onHealthCheck={checkConnectorHealth}
                    checkingHealth={Boolean(busy[`health:${connector.service}`])}
                    healthError={healthErrors[connector.service]}
                  />
                ))}
              </div>
            )}
          </Panel>

          <Panel
            eyebrow="Consumption"
            title="Used by agents and workflows"
            subtitle="Make the operational impact of each integration explicit before deeper build or runtime work begins."
          >
            <div className="space-y-3">
              {connectors.length > 0 ? connectors.map((connector) => (
                <SurfaceCard key={`${connector.service}:usage`} title={connector.display_name || humanize(connector.service)} eyebrow="Workflow access">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <ConnectorReadinessPill connector={connector} health={getConnectorHealth(connector)} />
                    <ConnectorHealthPill health={getConnectorHealth(connector)} />
                  </div>
                  <div className="mt-2 text-sm leading-7 text-muted-foreground">
                    Available to app-level workflows, agents, and operator tooling when required setup is complete.
                  </div>
                </SurfaceCard>
              )) : (
                <StudioInlineEmptyState
                  title="No workflow-linked integrations"
                  description="Connector usage will appear here once workflows or agents declare an external service dependency."
                />
              )}
            </div>
          </Panel>
        </div>

        <AdapterEditorOverlay
          open={Boolean(overlayMode)}
          mode={overlayMode}
          draft={draft}
          setDraft={setDraft}
          vaultConfigured={vaultConfigured}
          saving={overlayMode === 'edit' ? Boolean(busy[`save:${activeService}`]) : Boolean(busy.create)}
          deleting={Boolean(busy[`delete:${activeService}`])}
          onClose={closeOverlay}
          onSubmit={handleOverlaySubmit}
          onDelete={handleOverlayDelete}
        />
      </div>
    </WorkspaceLayout>
  )
}
