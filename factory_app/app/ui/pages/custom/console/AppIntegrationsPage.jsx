import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  API_BASE,
  ConsoleInlineEmptyState,
  StatusPill,
  Panel,
  SurfaceCard,
  ConsoleSlideOver,
  ConsoleLoadingState,
  ConsoleErrorState,
} from '../../../components/ConsoleShared.jsx'


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

function SavedSecretPill({ connector }) {
  return (
    <StatusPill tone={connector.secret_available ? 'success' : 'warning'}>
      {connector.secret_available ? 'Credential saved' : 'Credential not saved'}
    </StatusPill>
  )
}

function ConnectorCard({ connector, onEdit }) {
  const title = connector.display_name || humanize(connector.service)
  const notePreview = connector.notes?.trim() || null
  const updatedLabel = formatDateTime(connector.updated_at)
  const expiresLabel = formatDateTime(connector.expires_at)

  return (
    <SurfaceCard title={title} eyebrow="Integration">
      <div className="flex items-start justify-between gap-4">
        <SavedSecretPill connector={connector} />
        <ActionButton variant="secondary" onClick={() => onEdit(connector)}>
          Edit
        </ActionButton>
      </div>

      <div className="mt-4 space-y-3">
        {notePreview && <p className="text-sm leading-7 text-muted-foreground">{notePreview}</p>}
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground">
          {updatedLabel && <span>Updated {updatedLabel}</span>}
          {expiresLabel && <span>Expires {expiresLabel}</span>}
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
    <ConsoleSlideOver open={open} title={title} description={description} onClose={onClose} footer={footer}>
      <div className="space-y-5">
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Service
          </label>
          <input
            className="mt-2 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground disabled:opacity-70"
            value={draft.service}
            onChange={(event) => setDraft((prev) => ({ ...prev, service: event.target.value }))}
            placeholder="stripe"
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
            placeholder="Stripe"
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
    </ConsoleSlideOver>
  )
}

export default function AppIntegrationsPage() {
  const { appId = 'workspace-app' } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState({})
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

  if (loading) return <ConsoleLoadingState label="Loading Integrations…" />
  if (error || !data) return <ConsoleErrorState title="Integrations Unavailable" message={error || 'No integration data returned.'} />

  const vaultAdapter = data?.runtime_integrations?.connector_vault || data?.integrations?.connector_vault || null
  const connectors = Array.isArray(data.app_connectors) ? data.app_connectors : []
  const connectorSummary = data.connector_summary || {}
  const vaultConfigured = Boolean(vaultAdapter?.configured)
  const savedKeysCount = connectors.filter((connector) => Boolean(connector.secret_available)).length
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
      id: 'credentials',
      label: 'Saved Credentials',
      value: savedKeysCount,
      detail: vaultConfigured ? 'Vault-backed secrets available' : 'Vault not configured yet',
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
              <ConsoleInlineEmptyState
                title="No integrations registered yet"
                description="Add a service like Stripe, HubSpot, Slack, or another external provider when this app depends on it."
              />
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                {connectors.map((connector) => (
                  <ConnectorCard
                    key={connector.service}
                    connector={connector}
                    onEdit={openEditOverlay}
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
                <div key={`${connector.service}:usage`} className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="font-semibold text-foreground">{connector.display_name || humanize(connector.service)}</div>
                    <SavedSecretPill connector={connector} />
                  </div>
                  <div className="mt-2 text-sm leading-7 text-muted-foreground">
                    Available to app-level workflows, agents, and operator tooling once the connector is registered for this app.
                  </div>
                </div>
              )) : (
                <div className="rounded-[1.5rem] border border-dashed border-border/70 bg-background/55 px-4 py-6 text-sm text-muted-foreground">
                  No workflow-linked integrations have been recorded yet.
                </div>
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
