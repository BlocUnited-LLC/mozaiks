import { useEffect, useState } from 'react'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  API_BASE,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  StudioSlideOver,
} from '../../ui/components/StudioShared.jsx'

// ── helpers ──────────────────────────────────────────────────────────────────

function statusTone(status) {
  if (status === 'configured' || status === 'healthy') return 'success'
  if (status === 'partial' || status === 'pending_validation') return 'warning'
  if (status === 'missing' || status === 'unhealthy' || status === 'not_configured') return 'destructive'
  return 'default'
}

function statusLabel(status, item = null) {
  const resolvedStatus = item?.display_status || status
  if (resolvedStatus === 'configured') return 'Connected'
  if (resolvedStatus === 'partial') return 'Needs setup'
  if (resolvedStatus === 'missing' && Number(item?.app_usage_count || 0) > 0) return 'Needs setup'
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
  return null
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

// ── category avatar ──────────────────────────────────────────────────────────

const CATEGORY_COLORS = {
  ai:       { bg: 'bg-violet-500/15', text: 'text-violet-400',  border: 'border-violet-500/20' },
  database: { bg: 'bg-blue-500/15',   text: 'text-blue-400',    border: 'border-blue-500/20' },
  email:    { bg: 'bg-emerald-500/15',text: 'text-emerald-400', border: 'border-emerald-500/20' },
  payments: { bg: 'bg-orange-500/15', text: 'text-orange-400',  border: 'border-orange-500/20' },
  storage:  { bg: 'bg-amber-500/15',  text: 'text-amber-400',   border: 'border-amber-500/20' },
  sms:      { bg: 'bg-teal-500/15',   text: 'text-teal-400',    border: 'border-teal-500/20' },
  default:  { bg: 'bg-muted/40',      text: 'text-muted-foreground', border: 'border-border/40' },
}

function categoryColors(category) {
  return CATEGORY_COLORS[String(category || '').toLowerCase()] || CATEGORY_COLORS.default
}

function ServiceAvatar({ name, category }) {
  const colors = categoryColors(category)
  const initial = String(name || '?')[0].toUpperCase()
  return (
    <div
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border text-sm font-bold ${colors.bg} ${colors.text} ${colors.border}`}
    >
      {initial}
    </div>
  )
}

// ── status dot ───────────────────────────────────────────────────────────────

const DOT_COLORS = {
  success:     'bg-emerald-400',
  warning:     'bg-amber-400',
  destructive: 'bg-red-400',
  default:     'bg-muted-foreground/30',
}

function StatusDot({ tone }) {
  return (
    <span className={`inline-block h-2 w-2 rounded-full ${DOT_COLORS[tone] || DOT_COLORS.default}`} />
  )
}

// ── category filter tabs ──────────────────────────────────────────────────────

const ALL_TAB = 'all'

function CategoryTabs({ categories, active, onChange }) {
  const tabs = [ALL_TAB, ...categories]
  return (
    <div className="flex flex-wrap gap-1.5">
      {tabs.map((tab) => {
        const isActive = tab === active
        const colors = tab === ALL_TAB ? null : categoryColors(tab)
        return (
          <button
            key={tab}
            onClick={() => onChange(tab)}
            className={[
              'rounded-full px-3 py-1 text-xs font-medium transition-colors',
              isActive
                ? colors
                  ? `${colors.bg} ${colors.text} ${colors.border} border`
                  : 'bg-foreground/10 text-foreground border border-border'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted/40',
            ].join(' ')}
          >
            {tab === ALL_TAB ? 'All' : humanize(tab)}
          </button>
        )
      })}
    </div>
  )
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

async function checkWorkspaceConnector(service) {
  const res = await fetch(`${API_BASE}/api/modules/workspace_integrations/check_workspace_connector_health`, {
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
  onCheck,
  saving,
  deleting,
  checking,
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
  const tone = displayTone(item)

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
        {hasStoredConnector && (
          <ActionButton
            variant="secondary"
            onClick={() => onCheck(item.id)}
            disabled={saving || deleting || checking}
          >
            {checking ? 'Checking...' : 'Check connection'}
          </ActionButton>
        )}
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

        {/* Status + usage */}
        <div className="rounded-lg border border-border/55 bg-background/35 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={tone}>{statusLabel(item.status, item)}</StatusPill>
            {appUsageLabel(item) && (
              <StatusPill tone="muted">{appUsageLabel(item)}</StatusPill>
            )}
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

        {/* Credential source */}
        <div className="rounded-lg border border-border/55 bg-background/35 p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Credential source
          </div>
          <div className="mt-1 text-sm font-medium text-foreground">
            {connectorLabel(connector, item)}
          </div>
          {connector?.health?.status && (
            <div className="mt-2">
              <StatusPill tone={statusTone(connector.health.status)}>
                {humanize(connector.health.status)}
              </StatusPill>
            </div>
          )}
          {connector?.health?.message && (
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{connector.health.message}</p>
          )}
        </div>

        {/* Workspace note */}
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

        {/* Advanced */}
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
                      <span className="flex-none pt-0.5 text-xs font-semibold tabular-nums text-muted-foreground/60">
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

// ── IntegrationCard ───────────────────────────────────────────────────────────

function IntegrationCard({ item, connector, onOpen }) {
  const tone = displayTone(item)
  const isConnected = effectiveStatus(item) === 'configured' && !needsAttention(item)
  const isAttention = needsAttention(item)
  const usageLabel = appUsageLabel(item)

  // Left border accent communicates status without a second badge
  const borderAccent = isConnected
    ? 'border-l-2 border-l-emerald-500/60'
    : isAttention
    ? 'border-l-2 border-l-amber-500/60'
    : 'border-l-2 border-l-transparent'

  return (
    <div
      className={`flex flex-col gap-3 rounded-lg border border-border/50 bg-card/40 p-4 transition-colors hover:bg-card/60 ${borderAccent}`}
    >
      {/* Header: avatar + name + status dot */}
      <div className="flex items-start gap-3">
        <ServiceAvatar name={item.name} category={item.category} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-foreground leading-snug">{item.name}</h3>
            <StatusDot tone={tone} />
          </div>
          <span className={`text-xs font-medium ${categoryColors(item.category).text}`}>
            {humanize(item.category)}
          </span>
        </div>
      </div>

      {/* Description */}
      <p className="text-xs leading-5 text-muted-foreground line-clamp-2 flex-1">
        {item.description}
      </p>

      {/* Footer: usage + action */}
      <div className="flex items-center justify-between gap-2 border-t border-border/30 pt-3 mt-auto">
        <span className="text-xs text-muted-foreground/60">
          {usageLabel || (isConnected ? 'Ready for apps' : 'Not connected')}
        </span>
        <ActionButton
          variant="secondary"
          size="sm"
          onClick={() => onOpen(item)}
          className="shrink-0"
        >
          {actionLabel(item, connector)}
        </ActionButton>
      </div>
    </div>
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
  const [checking, setChecking] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [activeCategory, setActiveCategory] = useState(ALL_TAB)

  async function load(preserveCategory = false) {
    setLoading(true)
    try {
      const payload = await fetchIntegrations()
      setData(payload)
      // Default to AI on first load — LLM config is the prerequisite for everything else.
      if (!preserveCategory) {
        const cats = [
          ...new Set(
            (Array.isArray(payload.integrations) ? payload.integrations : [])
              .map((i) => String(i.category || '').toLowerCase())
              .filter(Boolean),
          ),
        ]
        setActiveCategory(cats.includes('ai') ? 'ai' : ALL_TAB)
      }
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
      await load(true)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Note could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDeleteConnector(service) {
    const connector = connectors.find((c) => c?.service === service)
    if (!connector) return
    const label = connector.display_name || activeItem?.name || humanize(service)
    if (!window.confirm(`Delete the saved workspace connector for ${label}?`)) return

    setDeleting(true)
    setActionError(null)
    try {
      await deleteWorkspaceConnector(service)
      setActiveItem(null)
      await load(true)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Connector could not be deleted.')
    } finally {
      setDeleting(false)
    }
  }

  async function handleCheckConnector(service) {
    const connector = connectors.find((c) => c?.service === service)
    if (!connector) return

    setChecking(true)
    setActionError(null)
    try {
      await checkWorkspaceConnector(service)
      await load(true)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Connector health check could not run.')
    } finally {
      setChecking(false)
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
      .filter((c) => c?.service)
      .map((c) => [c.service, c]),
  )
  const integrations = rawIntegrations.map((item) =>
    withConnectorOverlay(item, connectorsByService.get(item.id)),
  )
  const activeConnector = activeItem ? connectorsByService.get(activeItem.id) : null

  // Sort: attention first, then connected, then available
  const sorted = [
    ...integrations.filter(needsAttention),
    ...integrations.filter((i) => effectiveStatus(i) === 'configured' && !needsAttention(i)),
    ...integrations.filter((i) => !needsAttention(i) && effectiveStatus(i) !== 'configured'),
  ]

  // Derive categories in sorted order for filter tabs
  const categories = [...new Set(sorted.map((i) => String(i.category || '').toLowerCase()).filter(Boolean))]
  const filtered =
    activeCategory === ALL_TAB
      ? sorted
      : sorted.filter((i) => String(i.category || '').toLowerCase() === activeCategory)

  const attentionCount = integrations.filter(needsAttention).length
  const connectedCount = integrations.filter(
    (i) => effectiveStatus(i) === 'configured' && !needsAttention(i),
  ).length
  const usedCount = integrations.filter((i) => Number(i.app_usage_count || 0) > 0).length

  const summaryItems = [
    { id: 'connected', label: 'Connected', value: connectedCount, detail: 'Ready for apps' },
    {
      id: 'attention',
      label: 'Needs setup',
      value: attentionCount,
      detail: attentionCount > 0 ? 'Used by apps' : 'No blockers',
    },
    { id: 'used', label: 'Used by apps', value: summary.used ?? usedCount, detail: 'Declared by builds' },
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
          <div className="space-y-4">
            {categories.length > 1 && (
              <CategoryTabs
                categories={categories}
                active={activeCategory}
                onChange={setActiveCategory}
              />
            )}

            {filtered.length === 0 ? (
              <StudioInlineEmptyState
                title={`No ${humanize(activeCategory)} integrations`}
                description="Try a different category filter."
              />
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {filtered.map((item) => (
                  <IntegrationCard
                    key={item.id}
                    item={item}
                    connector={connectorsByService.get(item.id)}
                    onOpen={setActiveItem}
                  />
                ))}
              </div>
            )}
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
          onCheck={handleCheckConnector}
          saving={saving}
          deleting={deleting}
          checking={checking}
          actionError={actionError}
        />
      </div>
    </WorkspaceLayout>
  )
}
