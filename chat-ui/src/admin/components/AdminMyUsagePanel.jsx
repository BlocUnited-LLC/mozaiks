/**
 * AdminMyUsagePanel — admin portal panel showing the signed-in admin's
 * personal AI token balance and usage.
 *
 * Registered as a custom_component panel on the Usage admin page.
 * Fetches from /api/me/tokens (the same OSS endpoint the profile tab uses).
 *
 * Panel props: { panel, apiBaseUrl, auth }
 */

import { useState, useEffect, useCallback } from 'react'
import { SectionHeading } from './AdminPrimitives.jsx'

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

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function UsageBar({ used, allowance, unit }) {
  if (allowance == null || allowance <= 0) return null
  const pct = Math.min(100, Math.max(0, (used / allowance) * 100))
  const isLow = pct >= 80
  return (
    <div className="mt-2 space-y-1">
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-all ${isLow ? 'bg-warning' : 'bg-primary'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {used.toLocaleString()} of {allowance.toLocaleString()} {unit} used this period
        {isLow && <span className="ml-2 font-medium text-warning">— running low</span>}
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
    <div className="rounded-xl border border-border bg-card px-5 py-4">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm font-semibold text-foreground">{wallet.label || wallet.wallet_id}</span>
        <span className="tabular-nums text-sm font-medium text-foreground">
          {balance.toLocaleString()}{' '}
          <span className="font-normal text-muted-foreground">{unit} remaining</span>
        </span>
      </div>
      {used !== null && allowance !== null && (
        <UsageBar used={used} allowance={allowance} unit={unit} />
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

export default function AdminMyUsagePanel({ panel, apiBaseUrl = '', auth }) {
  const [wallets, setWallets] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const body = await fetchWithAuth(`${apiBaseUrl}/api/me/tokens`, auth)
      setWallets(body.wallets ?? [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [apiBaseUrl, auth])

  useEffect(() => { load() }, [load])

  if (loading) return <Spinner />
  if (error) return <ErrorBox message={`Could not load token balance: ${error}`} />
  if (!wallets || wallets.length === 0) {
    return <p className="text-sm text-muted-foreground italic">No token wallets configured.</p>
  }

  return (
    <div className="space-y-3">
      {wallets.map((wallet) => (
        <WalletRow key={wallet.wallet_id} wallet={wallet} />
      ))}
    </div>
  )
}
