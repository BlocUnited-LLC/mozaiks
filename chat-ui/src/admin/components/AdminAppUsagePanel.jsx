/**
 * AdminAppUsagePanel — admin portal panel showing aggregate AI token
 * consumption across all end users of this app.
 *
 * Registered as a custom_component panel on the Usage admin page.
 * Fetches from /api/admin/usage (admin-gated OSS endpoint) and renders
 * per-wallet balance totals sourced from the runtime token wallet ledger.
 *
 * Panel props: { panel, apiBaseUrl, auth }
 */

import { useState, useEffect, useCallback } from 'react'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function fetchWithAuth(url, auth) {
  const token = await auth?.getToken?.()
  const headers = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(url, { headers })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

function fmt(n) {
  return typeof n === 'number' ? n.toLocaleString() : '—'
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function WalletTotalRow({ wallet }) {
  const { label, wallet_id, total_balance_remaining, total_credited, total_debited, active_scopes } = wallet
  return (
    <div className="rounded-xl border border-border bg-card px-5 py-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-semibold text-foreground">{label || wallet_id}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{fmt(active_scopes)} active user scope{active_scopes !== 1 ? 's' : ''}</p>
        </div>
        <div className="text-right">
          <p className="tabular-nums text-sm font-medium text-foreground">{fmt(total_balance_remaining)} remaining</p>
          <p className="mt-0.5 tabular-nums text-xs text-muted-foreground">
            {fmt(total_credited)} credited · {fmt(total_debited)} used
          </p>
        </div>
      </div>
    </div>
  )
}

function UsageSummary({ usage }) {
  if (!usage) return null
  const totalTokens = usage.totals?.total_tokens
  const totalCalls = usage.totals?.total_calls ?? usage.totals?.event_count
  if (!totalTokens && !totalCalls) return null
  return (
    <div className="grid grid-cols-2 gap-3 mb-4">
      {totalTokens != null && (
        <div className="rounded-xl border border-border bg-card px-4 py-3 text-center">
          <p className="tabular-nums text-lg font-bold text-foreground">{fmt(totalTokens)}</p>
          <p className="text-xs text-muted-foreground mt-0.5">Total tokens consumed</p>
        </div>
      )}
      {totalCalls != null && (
        <div className="rounded-xl border border-border bg-card px-4 py-3 text-center">
          <p className="tabular-nums text-lg font-bold text-foreground">{fmt(totalCalls)}</p>
          <p className="text-xs text-muted-foreground mt-0.5">LLM calls</p>
        </div>
      )}
    </div>
  )
}

function Spinner() {
  return (
    <div className="flex items-center justify-center py-10">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
    </div>
  )
}

function ErrorBox({ message }) {
  return (
    <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
      {message}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export default function AdminAppUsagePanel({ panel, apiBaseUrl = '', auth }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const body = await fetchWithAuth(`${apiBaseUrl}/api/admin/usage`, auth)
      setData(body)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [apiBaseUrl, auth])

  useEffect(() => { load() }, [load])

  if (loading) return <Spinner />
  if (error) return <ErrorBox message={`Could not load app usage: ${error}`} />

  const walletTotals = data?.wallet_totals ?? []

  return (
    <div>
      <UsageSummary usage={data} />

      {walletTotals.length > 0 ? (
        <div className="space-y-3">
          {walletTotals.map((wallet) => (
            <WalletTotalRow key={wallet.wallet_id} wallet={wallet} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground italic">
          No wallet data yet. Token usage will appear here once users start consuming credits.
        </p>
      )}
    </div>
  )
}
