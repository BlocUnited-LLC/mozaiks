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
  if (String(key || '').toLowerCase() === 'llms') return 'LLMs'
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
  if (connector?.ready) return 'Workspace connector (active)'
  if (connector) return 'Workspace connector (incomplete)'
  if (item?.status === 'configured') return 'Environment variable'
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
    return 'This service is configured by a backend environment variable. To manage it here instead, enter the key below to save a workspace connector.'
  }
  if (Number(item?.app_usage_count || 0) > 0) {
    return 'At least one app needs this service, but the workspace does not have a complete connector or environment setup yet.'
  }
  return 'This service is available for future apps. No app currently depends on it.'
}

function actionLabel(item, connector) {
  if (needsAttention(item)) return 'Review setup'
  if (connector || effectiveStatus(item) === 'configured') return 'Manage'
  return 'Connect'
}

// ── category colors ───────────────────────────────────────────────────────────

const CATEGORY_COLORS = {
  llms:          { bg: 'bg-violet-500/15', text: 'text-violet-400',  border: 'border-violet-500/20' },
  database:      { bg: 'bg-blue-500/15',   text: 'text-blue-400',    border: 'border-blue-500/20' },
  email:         { bg: 'bg-emerald-500/15',text: 'text-emerald-400', border: 'border-emerald-500/20' },
  payments:      { bg: 'bg-orange-500/15', text: 'text-orange-400',  border: 'border-orange-500/20' },
  storage:       { bg: 'bg-amber-500/15',  text: 'text-amber-400',   border: 'border-amber-500/20' },
  sms:           { bg: 'bg-teal-500/15',   text: 'text-teal-400',    border: 'border-teal-500/20' },
  notifications: { bg: 'bg-sky-500/15',    text: 'text-sky-400',     border: 'border-sky-500/20' },
  auth:          { bg: 'bg-pink-500/15',   text: 'text-pink-400',    border: 'border-pink-500/20' },
  analytics:     { bg: 'bg-indigo-500/15', text: 'text-indigo-400',  border: 'border-indigo-500/20' },
  cache:         { bg: 'bg-rose-500/15',   text: 'text-rose-400',    border: 'border-rose-500/20' },
  default:       { bg: 'bg-muted/40',      text: 'text-muted-foreground', border: 'border-border/40' },
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
    <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${DOT_COLORS[tone] || DOT_COLORS.default}`} />
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

async function saveConnectorKey(service, secretValue, displayName) {
  const res = await fetch(`${API_BASE}/api/modules/workspace_integrations/save_workspace_connector`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ service, secret_value: secretValue, display_name: displayName }),
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
  onSaveKey,
  onSaveNote,
  onDelete,
  onCheck,
  saving,
  deleting,
  checking,
  actionError,
}) {
  const [note, setNote] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [copied, setCopied] = useState(null)
  const [keyVisible, setKeyVisible] = useState(false)

  useEffect(() => {
    if (open) {
      setNote(item?.note || '')
      setApiKey('')
      setKeyVisible(false)
    }
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
  const missingSecrets = secrets.filter((s) => !s.present)
  const hasStoredConnector = Boolean(connector?.service)
  const tone = displayTone(item)
  const isConnected = effectiveStatus(item) === 'configured' && !needsAttention(item)
  // Primary secret key name for the inline connect form (first required secret)
  const primaryKeyName = secrets[0]?.name || item.connector_key || null
  const setupUrl = item.setup_url || null

  const footer = (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        {hasStoredConnector ? (
          <ActionButton
            variant="destructive"
            onClick={() => onDelete(item.id)}
            disabled={saving || deleting}
          >
            {deleting ? 'Deleting...' : 'Remove connector'}
          </ActionButton>
        ) : (
          <p className="max-w-sm text-xs leading-5 text-muted-foreground">
            Remove appears only for saved workspace connectors.
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
            {checking ? 'Checking...' : 'Test connection'}
          </ActionButton>
        )}
        <ActionButton variant="secondary" onClick={onClose} disabled={saving || deleting}>
          Close
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

        {/* Status banner */}
        <div className={`flex items-center gap-3 rounded-lg border p-4 ${isConnected ? 'border-emerald-500/20 bg-emerald-500/8' : 'border-border/55 bg-background/35'}`}>
          <StatusDot tone={tone} />
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              {isConnected ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-semibold text-emerald-400 ring-1 ring-inset ring-emerald-500/25">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                  Connected
                </span>
              ) : (
                <span className="text-sm font-semibold text-foreground">
                  {statusLabel(item.status, item)}
                </span>
              )}
              {appUsageLabel(item) && (
                <StatusPill tone="muted">{appUsageLabel(item)}</StatusPill>
              )}
            </div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {connectorDescription(connector, item)}
            </p>
          </div>
        </div>

        {connectorError && (
          <p className="text-xs leading-5 text-muted-foreground px-1">
            Connector inventory unavailable: {connectorError}
          </p>
        )}

        {/* ── Key input form ── */}
        {primaryKeyName && (
          <div className="rounded-lg border border-border/55 bg-background/35 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold text-foreground">
                {isConnected ? 'Update API key' : 'Connect'}
              </div>
              {setupUrl && (
                <a
                  href={setupUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-muted-foreground hover:text-foreground underline underline-offset-2"
                >
                  Get API key ↗
                </a>
              )}
            </div>
            <div className="relative">
              <input
                type={keyVisible ? 'text' : 'password'}
                className="w-full rounded-md border border-border bg-background px-3 py-2 pr-20 font-mono text-sm text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-ring"
                placeholder={primaryKeyName}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setKeyVisible((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-muted-foreground hover:text-foreground px-1"
              >
                {keyVisible ? 'Hide' : 'Show'}
              </button>
            </div>
            <ActionButton
              onClick={() => onSaveKey(item.id, item.name, apiKey)}
              disabled={saving || !apiKey.trim()}
              className="w-full justify-center"
            >
              {saving ? 'Saving...' : isConnected ? 'Update key' : 'Save & connect'}
            </ActionButton>
          </div>
        )}

        {/* Connector health */}
        {connector?.health?.status && (
          <div className="rounded-lg border border-border/55 bg-background/35 p-4">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
              Last health check
            </div>
            <div className="flex items-center gap-2">
              <StatusPill tone={statusTone(connector.health.status)}>
                {humanize(connector.health.status)}
              </StatusPill>
            </div>
            {connector.health.message && (
              <p className="mt-2 text-sm leading-6 text-muted-foreground">{connector.health.message}</p>
            )}
          </div>
        )}

        {/* Workspace note */}
        <div>
          <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Workspace note
          </label>
          <textarea
            className="mt-2 min-h-20 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/50"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Owner, rotation schedule, environment scope..."
          />
          <div className="mt-2 flex justify-end">
            <ActionButton
              variant="secondary"
              size="sm"
              onClick={() => onSaveNote(item.id, note)}
              disabled={saving}
            >
              {saving ? 'Saving...' : 'Save note'}
            </ActionButton>
          </div>
        </div>

        {/* Advanced: env var status */}
        {secrets.length > 0 && (
          <details className="rounded-lg border border-border/55 bg-background/25 p-4">
            <summary className="cursor-pointer text-sm font-semibold text-foreground">
              Environment variable status
            </summary>
            <div className="mt-4 space-y-4">
              {missingSecrets.length > 0 && (
                <div className="rounded-md border border-warning/35 bg-warning/10 px-3 py-2 text-sm text-warning">
                  {missingSecrets.length} required env var{missingSecrets.length === 1 ? '' : 's'} not set. Use the connect form above to save a workspace connector instead.
                </div>
              )}
              <div className="space-y-2">
                {secrets.map((s) => (
                  <div
                    key={s.name}
                    className="flex items-center justify-between gap-3 rounded-md border border-border bg-background/60 px-3 py-2"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <StatusPill tone={s.present ? 'success' : 'default'}>
                        {s.present ? 'Set' : 'Not set'}
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
        )}
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

  const borderAccent = isConnected
    ? 'border-l-2 border-l-emerald-500/60'
    : isAttention
    ? 'border-l-2 border-l-amber-500/60'
    : 'border-l-2 border-l-transparent'

  return (
    <div
      className={`flex flex-col gap-3 rounded-lg border border-border/50 bg-card/40 p-4 transition-colors hover:bg-card/60 ${borderAccent}`}
    >
      {/* Header: avatar + name + status */}
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

      {/* Footer: status pill + action */}
      <div className="flex items-center justify-between gap-2 border-t border-border/30 pt-3 mt-auto">
        {usageLabel ? (
          <span className="text-xs font-medium text-muted-foreground/60">{usageLabel}</span>
        ) : isConnected ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-semibold text-emerald-400 ring-1 ring-inset ring-emerald-500/25">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            Connected
          </span>
        ) : isAttention ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-semibold text-amber-400 ring-1 ring-inset ring-amber-500/25">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
            Needs setup
          </span>
        ) : (
          <span className="text-xs font-medium text-muted-foreground/40">Not connected</span>
        )}
        <ActionButton
          variant={isAttention ? 'default' : 'secondary'}
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
      if (!preserveCategory) {
        const cats = [
          ...new Set(
            (Array.isArray(payload.integrations) ? payload.integrations : [])
              .map((i) => String(i.category || '').toLowerCase())
              .filter(Boolean),
          ),
        ]
        setActiveCategory(cats.includes('llms') ? 'llms' : ALL_TAB)
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

  async function handleSaveKey(service, displayName, keyValue) {
    if (!keyValue?.trim()) return
    setSaving(true)
    setActionError(null)
    try {
      await saveConnectorKey(service, keyValue.trim(), displayName)
      setActiveItem(null)
      await load(true)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Key could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  async function handleSaveNote(integrationId, note) {
    setSaving(true)
    setActionError(null)
    try {
      await saveNote(integrationId, note)
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
    const label = connector.display_name || activeItem?.name || service
    if (!window.confirm(`Remove the saved connector for ${label}?`)) return

    setDeleting(true)
    setActionError(null)
    try {
      await deleteWorkspaceConnector(service)
      setActiveItem(null)
      await load(true)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Connector could not be removed.')
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
      setActionError(err instanceof Error ? err.message : 'Health check could not run.')
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
    connectors.filter((c) => c?.service).map((c) => [c.service, c]),
  )
  const integrations = rawIntegrations.map((item) =>
    withConnectorOverlay(item, connectorsByService.get(item.id)),
  )
  const activeConnector = activeItem ? connectorsByService.get(activeItem.id) : null

  const sorted = [
    ...integrations.filter(needsAttention),
    ...integrations.filter((i) => effectiveStatus(i) === 'configured' && !needsAttention(i)),
    ...integrations.filter((i) => !needsAttention(i) && effectiveStatus(i) !== 'configured'),
  ]

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
    { id: 'attention', label: 'Needs setup', value: attentionCount, detail: attentionCount > 0 ? 'Used by apps' : 'No blockers' },
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
          onSaveKey={handleSaveKey}
          onSaveNote={handleSaveNote}
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
