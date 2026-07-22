/**
 * TokenStatusTab — profile tab showing a user's AI token balance.
 *
 * Rendered on /me when the app declares token_wallets in subscriptions.yaml.
 * Receives { tab, data } props from ProfilePage. The platform pre-hydrates
 * `data` via the billing_portal module's get_token_status action (mozaikspay
 * apps) or from the OSS built-in platform_builtin token tab injection.
 *
 * Data shape (from wallet_summaries_for_config or get_token_status):
 *   { wallets: [{ wallet_id, label, unit, balance, plan_allowances: [{token_amount}] }],
 *     plan_id, source }
 */

import { useState, useEffect, useCallback } from 'react'
import { useChatUI } from '../../context/ChatUIContext'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getApiBase(config, api) {
  if (api && typeof api.getHttpBaseUrl === 'function') {
    const base = api.getHttpBaseUrl()
    if (typeof base === 'string') return base.replace(/\/+$/, '')
  }
  return (
    config?.apiUrl ||
    config?.api_url ||
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
    ''
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function UsageBar({ used, allowance, unit }) {
  if (allowance == null || allowance <= 0) return null
  const pct = Math.min(100, Math.max(0, (used / allowance) * 100))
  const isLow = pct >= 80
  const barClass = isLow ? 'bg-warning' : 'bg-primary'

  return (
    <div className="mt-2 space-y-1">
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-all ${barClass}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {used.toLocaleString()} of {allowance.toLocaleString()} {unit} used this period
        {isLow && (
          <span className="ml-2 font-medium text-warning">— running low</span>
        )}
      </p>
    </div>
  )
}

function WalletRow({ wallet }) {
  const balance = wallet.balance ?? 0
  const allowance = wallet.plan_allowances?.[0]?.token_amount ?? null
  const used = allowance !== null ? Math.max(0, allowance - balance) : null
  const unit = wallet.unit || 'tokens'

  return (
    <div className="rounded-2xl border border-border bg-card px-5 py-4">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm font-semibold text-foreground">
          {wallet.label || wallet.wallet_id}
        </span>
        <span className="tabular-nums text-sm font-medium text-foreground">
          {balance.toLocaleString()}{' '}
          <span className="font-normal text-muted-foreground">{unit} remaining</span>
        </span>
      </div>

      {used !== null && allowance !== null && (
        <UsageBar used={used} allowance={allowance} unit={unit} />
      )}

      {allowance == null && (
        <p className="mt-1 text-xs text-muted-foreground">No allowance configured for current plan.</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Empty / error states
// ---------------------------------------------------------------------------

function Empty() {
  return (
    <div className="py-12 text-center">
      <p className="text-sm text-muted-foreground">No token wallets configured for this app.</p>
    </div>
  )
}

function ErrorBox({ message }) {
  return (
    <div className="rounded-2xl border border-destructive/30 bg-destructive/10 px-5 py-4 text-sm text-destructive">
      {message}
    </div>
  )
}

function Spinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

/**
 * TokenStatusTab
 *
 * Props:
 *   tab   — the tab descriptor from /api/me/profile-tabs
 *   data  — pre-hydrated wallet summary (may be null if hydration failed)
 */
export default function TokenStatusTab({ tab, data: preloaded }) {
  const { config, auth, api } = useChatUI()
  const apiBase = getApiBase(config, api)

  const [wallets, setWallets] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    // Use pre-hydrated data when present and valid
    if (preloaded?.wallets != null) {
      setWallets(preloaded.wallets)
      return
    }
    // Fall back to fetching /api/me/tokens
    if (!apiBase) {
      setWallets([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const token = await auth?.getToken?.()
      const headers = {}
      if (token) headers['Authorization'] = `Bearer ${token}`
      const res = await fetch(`${apiBase}/api/me/tokens`, { headers })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const body = await res.json()
      setWallets(body.wallets ?? [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [preloaded, apiBase, auth])

  useEffect(() => { load() }, [load])

  if (loading) return <Spinner />
  if (error) return <ErrorBox message={`Could not load token balance: ${error}`} />
  if (!wallets || wallets.length === 0) return <Empty />

  return (
    <div className="space-y-3">
      {wallets.map((wallet) => (
        <WalletRow key={wallet.wallet_id} wallet={wallet} />
      ))}
    </div>
  )
}
