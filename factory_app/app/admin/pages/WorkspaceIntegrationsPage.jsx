import { useEffect, useState } from 'react'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  API_BASE,
  Panel,
  SegmentedControl,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  StudioSlideOver,
  SurfaceCard,
} from '../../ui/components/StudioShared.jsx'

// ── helpers ──────────────────────────────────────────────────────────────────

function statusTone(status) {
  if (status === 'configured') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'missing') return 'destructive'
  return 'default'
}

function statusLabel(status) {
  const labels = {
    configured: 'Configured',
    partial: 'Partial',
    missing: 'Not configured',
    unknown: 'Unknown',
  }
  return labels[status] || status
}

function humanize(key) {
  return String(key || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function groupByCategory(integrations) {
  const map = new Map()
  for (const item of integrations) {
    const cat = item.category || 'other'
    if (!map.has(cat)) map.set(cat, [])
    map.get(cat).push(item)
  }
  return [...map.entries()].sort(([a], [b]) => a.localeCompare(b))
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

async function saveNote(integrationId, note) {
  const res = await fetch(`${API_BASE}/api/modules/workspace_integrations/set_integration_note`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ integration_id: integrationId, note }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// ── SetupSlideOver ────────────────────────────────────────────────────────────

function SetupSlideOver({ open, item, onClose, onSave, saving }) {
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
  const isConfigured = item.status === 'configured'

  const footer = (
    <div className="flex justify-end gap-3">
      <ActionButton variant="secondary" onClick={onClose} disabled={saving}>
        Close
      </ActionButton>
      <ActionButton onClick={() => onSave(item.id, note)} disabled={saving}>
        {saving ? 'Saving…' : 'Save note'}
      </ActionButton>
    </div>
  )

  return (
    <StudioSlideOver
      open={open}
      title={item.name}
      description={item.description || `${humanize(item.category)} integration`}
      onClose={onClose}
      footer={footer}
    >
      <div className="space-y-6">
        {/* Status */}
        <div className="flex items-center gap-3">
          <StatusPill tone={statusTone(item.status)}>
            {statusLabel(item.status)}
          </StatusPill>
          {isConfigured && (
            <span className="text-sm text-muted-foreground">All required credentials are set.</span>
          )}
        </div>

        {/* Required env vars */}
        {secrets.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Environment variables
            </div>
            <div className="space-y-2">
              {secrets.map((s) => (
                <div
                  key={s.name}
                  className="flex items-center justify-between gap-3 rounded-md border border-border bg-background/60 px-3 py-2"
                >
                  <div className="flex items-center gap-2 min-w-0">
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

        {/* Setup steps */}
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

        {/* Operator note */}
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Operator note
          </div>
          <textarea
            className="min-h-28 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/50"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Key rotation schedule, environment scope, who owns this integration…"
          />
        </div>
      </div>
    </StudioSlideOver>
  )
}

// ── IntegrationCard ───────────────────────────────────────────────────────────

function IntegrationCard({ item, onOpen }) {
  const needsSetup = item.status === 'missing' || item.status === 'partial'

  return (
    <SurfaceCard title={item.name} eyebrow={humanize(item.category)}>
      <div className="flex items-start justify-between gap-3">
        <StatusPill tone={statusTone(item.status)}>
          {statusLabel(item.status)}
        </StatusPill>
        <ActionButton variant="secondary" onClick={() => onOpen(item)}>
          {needsSetup ? 'Set up' : 'Details'}
        </ActionButton>
      </div>

      {item.note && (
        <p className="mt-3 text-sm leading-6 text-muted-foreground">{item.note}</p>
      )}

      {needsSetup && (
        <p className="mt-2 text-xs text-muted-foreground">
          {item.status === 'partial'
            ? 'Some credentials are missing. Open details to see which.'
            : 'Not configured. Open details for setup steps.'}
        </p>
      )}
    </SurfaceCard>
  )
}

// ── page ──────────────────────────────────────────────────────────────────────

export default function WorkspaceIntegrationsPage() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeItem, setActiveItem] = useState(null)
  const [saving, setSaving] = useState(false)
  const [activeCategory, setActiveCategory] = useState('__unset__')

  async function load() {
    setLoading(true)
    try {
      const payload = await fetchIntegrations()
      setData(payload)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load integrations.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleSaveNote(integrationId, note) {
    setSaving(true)
    try {
      await saveNote(integrationId, note)
      setActiveItem(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Note could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <StudioLoadingState label="Loading integrations…" />
  if (error || !data) return <StudioErrorState title="Integrations Unavailable" message={error || 'No data returned.'} />

  const integrations = Array.isArray(data.integrations) ? data.integrations : []
  const summary = data.summary || {}
  const allCategoryGroups = groupByCategory(integrations)
  const categories = allCategoryGroups.map(([cat]) => cat)
  const defaultCategory = categories.length > 0 ? categories[0] : null
  const resolvedCategory = activeCategory === '__unset__' ? defaultCategory : activeCategory
  const categoryGroups = resolvedCategory
    ? allCategoryGroups.filter(([cat]) => cat === resolvedCategory)
    : allCategoryGroups

  const summaryItems = [
    {
      id: 'total',
      label: 'In catalog',
      value: summary.total ?? integrations.length,
      detail: 'Known third-party services',
    },
    {
      id: 'configured',
      label: 'Configured',
      value: summary.configured ?? 0,
      detail: 'All credentials set',
    },
    {
      id: 'partial',
      label: 'Partial',
      value: summary.partial ?? 0,
      detail: 'Some credentials missing',
    },
    {
      id: 'missing',
      label: 'Not configured',
      value: summary.missing ?? 0,
      detail: 'No setup detected',
    },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Workspace Integrations"
          subtitle="Third-party services available to all apps in this workspace. Configure each service once — apps declare which ones they use."
        />

        <SummaryStrip items={summaryItems} />

        {categories.length > 1 && (
          <SegmentedControl
            options={[
              { value: null, label: 'All' },
              ...categories.map((cat) => ({ value: cat, label: humanize(cat) })),
            ]}
            value={resolvedCategory}
            onChange={setActiveCategory}
          />
        )}

        {integrations.length === 0 ? (
          <StudioInlineEmptyState
            title="No integrations in catalog"
            description="The integration catalog is empty."
          />
        ) : (
          <div className="space-y-6">
            {categoryGroups.map(([category, items]) => (
              <Panel
                key={category}
                eyebrow={resolvedCategory ? null : 'Category'}
                title={resolvedCategory ? null : humanize(category)}
              >
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {items.map((item) => (
                    <IntegrationCard
                      key={item.id}
                      item={item}
                      onOpen={setActiveItem}
                    />
                  ))}
                </div>
              </Panel>
            ))}
          </div>
        )}

        <SetupSlideOver
          open={Boolean(activeItem)}
          item={activeItem}
          onClose={() => setActiveItem(null)}
          onSave={handleSaveNote}
          saving={saving}
        />
      </div>
    </WorkspaceLayout>
  )
}
