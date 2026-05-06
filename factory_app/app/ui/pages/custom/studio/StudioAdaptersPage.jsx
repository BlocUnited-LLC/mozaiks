import { useEffect, useState } from 'react'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  ActionButton,
  API_BASE,
  StatusPill,
  SurfaceCard,
  StudioSlideOver,
  StudioLoadingState,
  StudioErrorState,
} from './StudioPrimitives.jsx'


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
    <SurfaceCard title={title} eyebrow="Adapter">
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
  const title = isEdit ? 'Edit Adapter' : 'Add Adapter'
  const description = isEdit
    ? 'Update the adapter record and replace its saved credential when needed.'
    : 'Register a third-party adapter for this app. Add a reusable credential later when credential saving is enabled.'
  const submitLabel = isEdit ? 'Save adapter' : 'Add adapter'
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
    </StudioSlideOver>
  )
}

export default function StudioAdaptersPage() {
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
      const res = await fetch(`${API_BASE}/api/studio/adapters`)
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const payload = await res.json()
      setData(payload)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Adapters could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false

    async function run() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/adapters`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) {
          setData(payload)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Adapters could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    run()
    return () => { cancelled = true }
  }, [])

  async function saveConnector(service, body) {
    setBusy((prev) => ({ ...prev, [`save:${service}`]: true }))
    try {
      const res = await fetch(`${API_BASE}/api/studio/adapters/connectors/${encodeURIComponent(service)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      await load()
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Adapter update failed.')
      return false
    } finally {
      setBusy((prev) => ({ ...prev, [`save:${service}`]: false }))
    }
  }

  async function deleteConnector(service) {
    setBusy((prev) => ({ ...prev, [`delete:${service}`]: true }))
    try {
      const res = await fetch(`${API_BASE}/api/studio/adapters/connectors/${encodeURIComponent(service)}`, {
        method: 'DELETE',
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      await load()
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Adapter deletion failed.')
      return false
    } finally {
      setBusy((prev) => ({ ...prev, [`delete:${service}`]: false }))
    }
  }

  async function createConnector(body) {
    setBusy((prev) => ({ ...prev, create: true }))
    try {
      const res = await fetch(`${API_BASE}/api/studio/adapters/connectors`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      await load()
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Adapter creation failed.')
      return false
    } finally {
      setBusy((prev) => ({ ...prev, create: false }))
    }
  }

  if (loading) return <StudioLoadingState label="Loading Adapters…" />
  if (error || !data) return <StudioErrorState title="Adapters Unavailable" message={error || 'No adapter data returned.'} />

  const vaultAdapter = data?.runtime_adapters?.connector_vault || data?.adapters?.connector_vault || null
  const connectors = Array.isArray(data.app_connectors) ? data.app_connectors : []
  const connectorSummary = data.connector_summary || {}
  const vaultConfigured = Boolean(vaultAdapter?.configured)
  const savedKeysCount = connectors.filter((connector) => Boolean(connector.secret_available)).length
  const editingConnector = connectors.find((connector) => connector.service === activeService) || null

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
    <AdminWorkspaceLayout>
      <div className="flex flex-col gap-6">
        <SurfaceCard title="External Adapters" eyebrow="Studio" accent>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="max-w-3xl text-sm leading-7 text-muted-foreground">
                Track third-party services connected to this app. Host wiring like models, database, auth, and backend settings live elsewhere so this page stays focused on external integrations.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <StatusPill tone="primary">{connectorSummary.total || 0} adapters</StatusPill>
                <StatusPill tone={savedKeysCount > 0 ? 'success' : 'warning'}>{savedKeysCount} saved credentials</StatusPill>
                <StatusPill tone={vaultConfigured ? 'success' : 'warning'}>
                  {vaultConfigured ? 'Credential saving ready' : 'Credential saving not set up'}
                </StatusPill>
              </div>
            </div>
            <ActionButton onClick={openCreateOverlay}>
              Add Adapter
            </ActionButton>
          </div>
        </SurfaceCard>

        {connectors.length === 0 ? (
          <SurfaceCard title="No External Adapters Yet" eyebrow="Adapters">
            <p className="text-sm leading-7 text-muted-foreground">
              No app-level third-party adapters have been recorded yet. Add one when this app depends on a service like Stripe, HubSpot, Slack, or another external provider.
            </p>
            <div className="mt-4">
              <ActionButton onClick={openCreateOverlay}>
              Add Adapter
              </ActionButton>
            </div>
          </SurfaceCard>
        ) : (
          <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3">
            {connectors.map((connector) => (
              <ConnectorCard
                key={connector.service}
                connector={connector}
                onEdit={openEditOverlay}
              />
            ))}
          </div>
        )}

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
    </AdminWorkspaceLayout>
  )
}