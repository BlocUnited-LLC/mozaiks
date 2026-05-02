import { useEffect, useState } from 'react'

import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  StatusPill,
  SurfaceCard,
  StudioLoadingState,
  StudioErrorState,
} from './StudioPrimitives.jsx'


function DetailRow({ label, value, mono = false }) {
  if (!value) return null
  return (
    <div className="flex items-start gap-2 text-sm">
      <span className="w-32 shrink-0 font-semibold text-foreground">{label}</span>
      <span className={`min-w-0 break-all text-muted-foreground ${mono ? 'font-mono text-xs' : ''}`}>{value}</span>
    </div>
  )
}

function AdapterDetails({ adapter }) {
  switch (adapter.kind) {
    case 'llm':
      return (
        <>
          <DetailRow label="Source" value={adapter.source ? `${adapter.source}` : 'not set'} />
          <DetailRow label="Primary Model" value={adapter.primary_model} />
          {adapter.fallback_models?.length > 0 && (
            <DetailRow label="Fallbacks" value={adapter.fallback_models.join(', ')} />
          )}
          <DetailRow label="API Key" value={adapter.api_key_set ? adapter.api_key_masked : 'not set'} mono />
        </>
      )
    case 'database':
      return (
        <DetailRow label="Connection" value={adapter.configured ? adapter.uri_masked : 'MONGO_URI not set'} mono />
      )
    case 'sandbox':
      return (
        <DetailRow label="API Key" value={adapter.api_key_set ? adapter.api_key_masked : 'E2B_API_KEY not set'} mono />
      )
    case 'auth': {
      const provider = adapter.provider || (adapter.keycloak ? 'keycloak' : adapter.supabase ? 'supabase' : null)
      return (
        <>
          <DetailRow label="Enabled" value={adapter.enabled ? 'yes' : 'no'} />
          {provider && <DetailRow label="Provider" value={provider} />}
          {adapter.keycloak?.url && <DetailRow label="Keycloak URL" value={adapter.keycloak.url} mono />}
          {adapter.keycloak?.realm && <DetailRow label="Realm" value={adapter.keycloak.realm} />}
          {adapter.keycloak?.client_id && <DetailRow label="Client ID" value={adapter.keycloak.client_id} />}
          {adapter.supabase?.url && <DetailRow label="Supabase URL" value={adapter.supabase.url} mono />}
          {adapter.jwks_url && <DetailRow label="JWKS URL" value={adapter.jwks_url} mono />}
          {!adapter.enabled && (
            <p className="text-sm text-muted-foreground">Set AUTH_ENABLED=true to enable authentication.</p>
          )}
        </>
      )
    }
    case 'backend':
      return (
        <>
          <DetailRow label="Backend URL" value={adapter.url || 'MOZAIKS_BACKEND_URL not set'} mono />
          <DetailRow label="Internal Key" value={adapter.internal_key_set ? adapter.internal_key_masked : 'not set'} mono />
        </>
      )
    case 'vault':
      return <DetailRow label="Vault Name" value={adapter.vault_name} />
    default:
      return null
  }
}

function AdapterCard({ adapter }) {
  const tone = adapter.configured ? 'success' : 'warning'
  const statusLabel = adapter.configured ? 'Configured' : 'Not configured'

  return (
    <SurfaceCard title={adapter.label} eyebrow={adapter.kind}>
      <div className="mb-4">
        <StatusPill tone={tone}>{statusLabel}</StatusPill>
      </div>
      <div className="space-y-2">
        <AdapterDetails adapter={adapter} />
      </div>
    </SurfaceCard>
  )
}

const KIND_ORDER = ['llm', 'database', 'sandbox', 'auth', 'backend', 'vault']

function sortedAdapters(adapters) {
  return Object.values(adapters).sort((a, b) => {
    const ia = KIND_ORDER.indexOf(a.kind)
    const ib = KIND_ORDER.indexOf(b.kind)
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
  })
}

export default function StudioAdaptersPage() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/adapters`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) { setData(payload); setError(null) }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Adapters could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  if (loading) return <StudioLoadingState label="Loading Adapters…" />
  if (error || !data) return <StudioErrorState title="Adapters Unavailable" message={error || 'No adapter data returned.'} />

  const adapters = sortedAdapters(data.adapters || {})
  const allConfigured = adapters.every((a) => a.configured)
  const unconfigured = adapters.filter((a) => !a.configured)

  return (
    <AdminWorkspaceLayout>
      <div className="flex flex-col gap-6">
        <SurfaceCard title="Adapters" eyebrow="Studio" accent>
          <p className="max-w-2xl text-sm leading-7 text-muted-foreground">
            Runtime connections and API keys. Keys are masked — configure them via environment variables or Azure Key Vault.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <StatusPill tone={allConfigured ? 'success' : 'warning'}>
              {allConfigured ? 'All configured' : `${unconfigured.length} not configured`}
            </StatusPill>
            <StatusPill tone="primary">{adapters.length} adapters</StatusPill>
          </div>
        </SurfaceCard>

        <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3">
          {adapters.map((adapter) => (
            <AdapterCard key={adapter.kind} adapter={adapter} />
          ))}
        </div>
      </div>
    </AdminWorkspaceLayout>
  )
}