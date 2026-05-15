import { useEffect, useMemo, useState } from 'react'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  API_BASE,
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  ConsoleSlideOver,
  Panel,
  StatusPill,
} from '../../ui/components/ConsoleShared.jsx'
import { WorkspaceConsoleHero, formatCompactNumber } from './AppConsoleChrome.jsx'
import { useWorkspaceConsoleData } from './useWorkspaceConsoleData.js'

// ─── Integration catalog ─────────────────────────────────────────────────────
const CATALOG = [
  {
    id: 'stripe',
    name: 'Stripe',
    category: 'Payments',
    description: 'Accept payments, manage subscriptions, and track revenue.',
    color: 'bg-primary/10 text-primary',
    fields: [
      { key: 'publishable_key', label: 'Publishable Key', placeholder: 'pk_live_…', secret: false },
      { key: 'secret_key', label: 'Secret Key', placeholder: 'sk_live_…', secret: true },
      { key: 'webhook_secret', label: 'Webhook Secret', placeholder: 'whsec_…', secret: true },
    ],
  },
  {
    id: 'resend',
    name: 'Resend',
    category: 'Email',
    description: 'Transactional email delivery for developers.',
    color: 'bg-success/10 text-success',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 're_…', secret: true },
      { key: 'from_address', label: 'Default From Address', placeholder: 'hello@yourdomain.com', secret: false },
    ],
  },
  {
    id: 'sendgrid',
    name: 'SendGrid',
    category: 'Email',
    description: 'Cloud-based email delivery and marketing automation.',
    color: 'bg-primary/10 text-primary',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'SG.…', secret: true },
      { key: 'from_address', label: 'From Address', placeholder: 'no-reply@yourdomain.com', secret: false },
    ],
  },
  {
    id: 'twilio',
    name: 'Twilio',
    category: 'SMS & Voice',
    description: 'Programmable SMS, voice, and messaging APIs.',
    color: 'bg-destructive/10 text-destructive',
    fields: [
      { key: 'account_sid', label: 'Account SID', placeholder: 'AC…', secret: false },
      { key: 'auth_token', label: 'Auth Token', placeholder: '', secret: true },
      { key: 'phone_number', label: 'Phone Number', placeholder: '+1…', secret: false },
    ],
  },
  {
    id: 'slack',
    name: 'Slack',
    category: 'Notifications',
    description: 'Send alerts and notifications to Slack channels.',
    color: 'bg-warning/10 text-warning',
    fields: [
      { key: 'webhook_url', label: 'Incoming Webhook URL', placeholder: 'https://hooks.slack.com/services/…', secret: true },
      { key: 'bot_token', label: 'Bot Token (optional)', placeholder: 'xoxb-…', secret: true },
      { key: 'default_channel', label: 'Default Channel', placeholder: '#alerts', secret: false },
    ],
  },
  {
    id: 'openai',
    name: 'OpenAI',
    category: 'AI',
    description: 'GPT models, embeddings, and image generation APIs.',
    color: 'bg-success/10 text-success',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'sk-…', secret: true },
      { key: 'org_id', label: 'Organization ID (optional)', placeholder: 'org-…', secret: false },
    ],
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    category: 'AI',
    description: 'Claude AI models for reasoning, code, and generation.',
    color: 'bg-secondary/10 text-secondary-foreground',
    fields: [
      { key: 'api_key', label: 'API Key', placeholder: 'sk-ant-…', secret: true },
    ],
  },
  {
    id: 'aws_s3',
    name: 'AWS S3',
    category: 'Storage',
    description: 'Object storage for files, media, and app assets.',
    color: 'bg-warning/10 text-warning',
    fields: [
      { key: 'access_key_id', label: 'Access Key ID', placeholder: 'AKIA…', secret: false },
      { key: 'secret_access_key', label: 'Secret Access Key', placeholder: '', secret: true },
      { key: 'bucket_name', label: 'Bucket Name', placeholder: 'my-app-bucket', secret: false },
      { key: 'region', label: 'Region', placeholder: 'us-east-1', secret: false },
    ],
  },
  {
    id: 'mixpanel',
    name: 'Mixpanel',
    category: 'Analytics',
    description: 'Product analytics — events, funnels, and user retention.',
    color: 'bg-primary/10 text-primary',
    fields: [
      { key: 'project_token', label: 'Project Token', placeholder: '', secret: false },
      { key: 'api_secret', label: 'API Secret', placeholder: '', secret: true },
    ],
  },
  {
    id: 'hubspot',
    name: 'HubSpot',
    category: 'CRM',
    description: 'CRM, marketing automation, and customer lifecycle management.',
    color: 'bg-destructive/10 text-destructive',
    fields: [
      { key: 'access_token', label: 'Private App Token', placeholder: 'pat-na1-…', secret: true },
    ],
  },
]

const DEMO_CONNECTED = {
  stripe: { apps: [] },
  resend: { apps: [] },
  slack: { apps: [] },
}

const CATEGORIES = ['All', ...Array.from(new Set(CATALOG.map((i) => i.category)))]

// ─── Sub-components ───────────────────────────────────────────────────────────

function IntegrationAvatar({ integration }) {
  return (
    <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-sm font-bold shrink-0 ${integration.color}`}>
      {integration.name.slice(0, 2)}
    </div>
  )
}

function CategoryBadge({ category }) {
  return (
    <span className="inline-flex items-center rounded-full border border-border bg-muted px-2 py-0.5 text-[0.65rem] font-medium text-muted-foreground">
      {category}
    </span>
  )
}

function AppChip({ name }) {
  return (
    <span className="inline-flex items-center rounded-full bg-primary/10 border border-primary/20 px-2 py-0.5 text-[0.65rem] font-medium text-primary">
      {name}
    </span>
  )
}

function IntegrationCard({ integration, connector, appNames, onConnect, onManage }) {
  const connected = Boolean(connector)
  return (
    <div className="rounded-2xl border border-border bg-background p-5 flex flex-col gap-4 shadow-sm">
      <div className="flex items-start gap-3">
        <IntegrationAvatar integration={integration} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-foreground text-sm">{integration.name}</span>
            <CategoryBadge category={integration.category} />
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground leading-relaxed">{integration.description}</p>
        </div>
        <div className="shrink-0">
          <StatusPill tone={connected ? 'success' : 'muted'}>
            {connected ? 'Connected' : 'Available'}
          </StatusPill>
        </div>
      </div>

      {connected && appNames.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {appNames.map((name) => <AppChip key={name} name={name} />)}
        </div>
      )}

      {connected && appNames.length === 0 && (
        <p className="text-xs text-muted-foreground">No apps assigned yet.</p>
      )}

      <div className="flex items-center gap-2 mt-auto">
        {connected ? (
          <ActionButton
            size="sm"
            variant="outline"
            className="border-primary/35 text-primary hover:bg-primary/10 hover:text-primary"
            onClick={() => onManage(integration, connector)}
          >
            Manage
          </ActionButton>
        ) : (
          <ActionButton size="sm" variant="secondary" className="font-semibold" onClick={() => onConnect(integration)}>
            Connect
          </ActionButton>
        )}
      </div>
    </div>
  )
}

function IntegrationSlideOver({ open, mode, integration, apps, draft, setDraft, saving, onClose, onSubmit, onDisconnect, onDelete }) {
  if (!integration) return null
  const isManage = mode === 'manage'
  const title = isManage ? `Manage ${integration.name}` : `Connect ${integration.name}`
  const description = isManage
    ? `Update credentials or change which apps use ${integration.name}.`
    : `Add your ${integration.name} credentials. Secrets are stored securely and never exposed to the frontend.`

  const footer = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap gap-2">
        {isManage && (
          <>
            <ActionButton variant="secondary" disabled={saving} onClick={onDisconnect}>
              Disconnect
            </ActionButton>
            <ActionButton variant="destructive" disabled={saving} onClick={onDelete}>
              Delete
            </ActionButton>
          </>
        )}
      </div>
      <div className="flex gap-3">
        <ActionButton variant="secondary" onClick={onClose} disabled={saving}>Cancel</ActionButton>
        <ActionButton onClick={onSubmit} disabled={saving}>
          {saving ? 'Saving…' : isManage ? 'Save changes' : 'Connect'}
        </ActionButton>
      </div>
    </div>
  )

  return (
    <ConsoleSlideOver open={open} title={title} description={description} onClose={onClose} footer={footer}>
      <div className="space-y-5">
        {/* Credential fields */}
        <div className="space-y-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Credentials</p>
          {integration.fields.map((field) => (
            <div key={field.key}>
              <label className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {field.label}
              </label>
              <input
                type={field.secret ? 'password' : 'text'}
                className="mt-2 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
                placeholder={isManage && field.secret ? 'Leave blank to keep current value' : field.placeholder}
                value={draft.fields?.[field.key] || ''}
                onChange={(e) =>
                  setDraft((prev) => ({
                    ...prev,
                    fields: { ...prev.fields, [field.key]: e.target.value },
                  }))
                }
              />
            </div>
          ))}
        </div>

        {/* App assignment */}
        {apps.length > 0 && (
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Assign to apps</p>
            <p className="text-xs text-muted-foreground">Select which apps can use this integration's credentials.</p>
            <div className="space-y-2">
              {apps.map((app) => {
                const appId = app.app?.app_id || app.id
                const assigned = (draft.assignedApps || []).includes(appId)
                return (
                  <label key={appId} className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 cursor-pointer hover:bg-muted/40 transition-colors">
                    <input
                      type="checkbox"
                      checked={assigned}
                      onChange={() =>
                        setDraft((prev) => {
                          const current = prev.assignedApps || []
                          const isAssigned = current.includes(appId)
                          return {
                            ...prev,
                            assignedApps: isAssigned
                              ? current.filter((id) => id !== appId)
                              : [...current, appId],
                          }
                        })
                      }
                      className="rounded border-border"
                    />
                    <div>
                      <div className="text-sm font-medium text-foreground">{app.name}</div>
                      {app.description && (
                        <div className="text-xs text-muted-foreground">{app.description}</div>
                      )}
                    </div>
                  </label>
                )
              })}
            </div>
          </div>
        )}

        {/* Notes */}
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Notes (optional)
          </label>
          <textarea
            className="mt-2 min-h-24 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground"
            placeholder="Rotation schedule, account owner, environment notes…"
            value={draft.notes || ''}
            onChange={(e) => setDraft((prev) => ({ ...prev, notes: e.target.value }))}
          />
        </div>
      </div>
    </ConsoleSlideOver>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

const EMPTY_DRAFT = { fields: {}, assignedApps: [], notes: '' }

export default function WorkspaceIntegrationsPage() {
  const { apps, loading: appsLoading, dataMode } = useWorkspaceConsoleData('Workspace integrations could not be loaded.')
  const [connectors, setConnectors] = useState(null)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('All')
  const [overlayOpen, setOverlayOpen] = useState(false)
  const [overlayMode, setOverlayMode] = useState(null)
  const [activeIntegration, setActiveIntegration] = useState(null)
  const [draft, setDraft] = useState(EMPTY_DRAFT)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/integrations?scope=workspace`)
        if (res.ok) {
          const payload = await res.json()
          if (!cancelled) setConnectors(payload.connectors || [])
        } else {
          if (!cancelled) setConnectors(null)
        }
      } catch {
        if (!cancelled) setConnectors(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  // Fall back to demo state when no real data
  const connectorMap = useMemo(() => {
    if (connectors && connectors.length > 0) {
      return Object.fromEntries(connectors.map((c) => [c.service, c]))
    }
    if (dataMode === 'demo') return DEMO_CONNECTED
    return {}
  }, [connectors, dataMode])

  const visibleCatalog = useMemo(() => {
    let items = CATALOG
    if (category !== 'All') items = items.filter((i) => i.category === category)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      items = items.filter(
        (i) => i.name.toLowerCase().includes(q) || i.category.toLowerCase().includes(q) || i.description.toLowerCase().includes(q),
      )
    }
    return items
  }, [category, search])

  const connectedCount = CATALOG.filter((i) => connectorMap[i.id]).length

  const summaryItems = [
    { id: 'connected', label: 'Connected', value: formatCompactNumber(connectedCount, '0'), detail: 'Active integrations' },
    { id: 'available', label: 'Available', value: formatCompactNumber(CATALOG.length - connectedCount, '0'), detail: 'Ready to connect' },
    { id: 'categories', label: 'Categories', value: CATEGORIES.length - 1, detail: 'Service types' },
    {
      id: 'apps',
      label: 'App Assignments',
      value: formatCompactNumber(Object.values(connectorMap).reduce((n, c) => n + (c.apps?.length || 0), 0), '0'),
      detail: 'App–integration links',
    },
  ]

  function openConnect(integration) {
    setActiveIntegration(integration)
    setDraft(EMPTY_DRAFT)
    setOverlayMode('connect')
    setOverlayOpen(true)
  }

  function openManage(integration, connector) {
    setActiveIntegration(integration)
    setDraft({ fields: {}, assignedApps: connector.apps || [], notes: connector.notes || '' })
    setOverlayMode('manage')
    setOverlayOpen(true)
  }

  function closeOverlay() {
    setOverlayOpen(false)
    setActiveIntegration(null)
    setDraft(EMPTY_DRAFT)
    setOverlayMode(null)
  }

  async function handleSubmit() {
    setSaving(true)
    try {
      const body = {
        service: activeIntegration.id,
        fields: draft.fields,
        assigned_apps: draft.assignedApps,
        notes: draft.notes,
        scope: 'workspace',
      }
      const url = overlayMode === 'manage'
        ? `${API_BASE}/api/studio/integrations/connectors/${activeIntegration.id}`
        : `${API_BASE}/api/studio/integrations/connectors`
      const method = overlayMode === 'manage' ? 'PATCH' : 'POST'
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (res.ok) {
        const payload = await res.json()
        setConnectors((prev) => {
          const list = prev || []
          const exists = list.findIndex((c) => c.service === activeIntegration.id)
          if (exists >= 0) {
            const next = [...list]
            next[exists] = payload.connector || { service: activeIntegration.id, ...body }
            return next
          }
          return [...list, payload.connector || { service: activeIntegration.id, ...body }]
        })
      }
    } catch {
      // keep overlay open on error; user can retry
    } finally {
      setSaving(false)
      closeOverlay()
    }
  }

  async function handleDisconnect() {
    // Remove from all apps but keep credentials saved
    if (!activeIntegration) return
    setSaving(true)
    try {
      await fetch(`${API_BASE}/api/studio/integrations/connectors/${activeIntegration.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ assigned_apps: [], scope: 'workspace' }),
      })
      setConnectors((prev) => (prev || []).map((c) =>
        c.service === activeIntegration.id ? { ...c, apps: [] } : c,
      ))
    } catch {
      // ignore
    } finally {
      setSaving(false)
      closeOverlay()
    }
  }

  async function handleDelete() {
    // Permanently remove credentials and connector record
    if (!activeIntegration) return
    setSaving(true)
    try {
      await fetch(`${API_BASE}/api/studio/integrations/connectors/${activeIntegration.id}`, { method: 'DELETE' })
      setConnectors((prev) => (prev || []).filter((c) => c.service !== activeIntegration.id))
    } catch {
      // ignore
    } finally {
      setSaving(false)
      closeOverlay()
    }
  }

  if (loading || appsLoading) return <ConsoleLoadingState label="Loading integrations…" />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceConsoleHero
          title="Integrations"
          subtitle="Connect external services once at the workspace level and assign them to any of your apps. Credentials are stored securely and never exposed to the frontend."
          summaryItems={summaryItems}
        />

        <Panel
          eyebrow="Integration catalog"
          title="Available services"
          subtitle="Connect a service to save its credentials and assign it to one or more apps."
        >
          {/* Search + category filter */}
          <div className="mb-5 flex flex-wrap gap-3 items-center">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search integrations…"
              className="rounded-[var(--shell-control-radius,1rem)] border border-border bg-card px-4 py-2.5 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20 w-56"
            />
            <div className="flex flex-wrap gap-2">
              {CATEGORIES.map((cat) => (
                <button
                  key={cat}
                  onClick={() => setCategory(cat)}
                  className={[
                    'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                    cat === category
                      ? 'border-primary/40 bg-primary/10 text-primary'
                      : 'border-border bg-card text-muted-foreground hover:text-foreground hover:border-border/80',
                  ].join(' ')}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>

          {visibleCatalog.length === 0 ? (
            <ConsoleInlineEmptyState
              title="No integrations match"
              description="Try a different search term or category."
            />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {visibleCatalog.map((integration) => (
                <IntegrationCard
                  key={integration.id}
                  integration={integration}
                  connector={connectorMap[integration.id] || null}
                  appNames={(connectorMap[integration.id]?.apps || []).map((id) => {
                    const match = apps.find((a) => (a.app?.app_id || a.id) === id)
                    return match?.name || id
                  })}
                  onConnect={openConnect}
                  onManage={openManage}
                />
              ))}
            </div>
          )}
        </Panel>
      </div>

      <IntegrationSlideOver
        open={overlayOpen}
        mode={overlayMode}
        integration={activeIntegration}
        apps={apps}
        draft={draft}
        setDraft={setDraft}
        saving={saving}
        onClose={closeOverlay}
        onSubmit={handleSubmit}
        onDisconnect={handleDisconnect}
        onDelete={handleDelete}
      />
    </WorkspaceLayout>
  )
}
