/**
 * ConnectedAccountsTab — profile tab where a user connects their own
 * third-party accounts (Google Calendar, Gmail, ...) to this app.
 *
 * Rendered on /me?tab=connected-accounts when an installed plugin declares an
 * account connection. Lists the connections the app offers with this user's
 * status, and the accounts the user has connected, which they can disconnect.
 * A plugin action that needs a connection sends the user here with
 * ?connect=<connection_id>, which highlights that connection.
 *
 * Every call is the signed-in user's own: GET /api/me/connected-accounts,
 * POST /api/me/connected-accounts/{id}/connect (returns the provider's
 * authorize URL, opened in a popup), GET /api/me/connected-accounts/flows/{id}
 * (polled until the popup's flow ends) and
 * DELETE /api/me/connected-accounts/providers/{provider}.
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import { authFetch } from '../../adapters/api.js'

const POPUP_FEATURES = 'width=520,height=680,scrollbars=yes,resizable=yes'
const POLL_INTERVAL_MS = 1000
const PROVIDER_URL_RE = /^https?:\/\//i

const STATUS_TEXT = {
  connected: 'Connected',
  not_connected: 'Not connected',
  reconnect_required: 'Reconnect needed',
  unavailable: 'Not available yet',
}

const STATUS_CLASS = {
  connected: 'bg-success/15 text-success',
  not_connected: 'bg-muted text-muted-foreground',
  reconnect_required: 'bg-warning/15 text-warning',
  unavailable: 'bg-muted text-muted-foreground',
}

async function readJson(response) {
  try { return await response.json() } catch (_) { return null }
}

function useConnectPopup(onConnected) {
  const popupRef = useRef(null)
  const pollRef = useRef(null)
  const mountedRef = useRef(true)
  const [pendingId, setPendingId] = useState(null)
  const [message, setMessage] = useState(null) // { kind: 'error' | 'success', text }

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  const closePopup = useCallback(() => {
    if (popupRef.current && !popupRef.current.closed) popupRef.current.close()
    popupRef.current = null
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false; stopPolling() }
  }, [stopPolling])

  const connect = useCallback(async (connection) => {
    // Open the popup inside the click so it is not blocked, then send it to the
    // provider once the server has started a flow bound to this user and browser.
    stopPolling()
    closePopup()
    const popup = window.open('', 'mozaiks_account_connect', POPUP_FEATURES)
    if (!popup) {
      setMessage({ kind: 'error', text: 'Allow pop-ups for this site to connect an account.' })
      return
    }
    popupRef.current = popup
    setPendingId(connection.id)
    setMessage(null)

    let started = null
    let failure = null
    try {
      // credentials: 'include' stores the flow's browser-binding cookie even when
      // the API is served from another origin of the same site.
      const response = await authFetch(
        `/api/me/connected-accounts/${encodeURIComponent(connection.id)}/connect`,
        { method: 'POST', credentials: 'include' },
      )
      const body = await readJson(response)
      if (response.ok) started = body
      else failure = typeof body?.detail === 'string' ? body.detail : null
    } catch (_) { /* reported below */ }
    if (!mountedRef.current) { closePopup(); return }
    const authorizeUrl = typeof started?.authorize_url === 'string' ? started.authorize_url : ''
    const flowId = typeof started?.flow_id === 'string' ? started.flow_id : ''
    if (popup.closed) { popupRef.current = null; setPendingId(null); return }
    if (!flowId || !PROVIDER_URL_RE.test(authorizeUrl)) {
      closePopup()
      setPendingId(null)
      setMessage({ kind: 'error', text: failure || `Could not start connecting ${connection.label}. Try again.` })
      return
    }
    popup.location.href = authorizeUrl

    let pollsAfterClose = 0
    pollRef.current = setInterval(async () => {
      const popupClosed = !popupRef.current || popupRef.current.closed
      try {
        const response = await authFetch(`/api/me/connected-accounts/flows/${encodeURIComponent(flowId)}`)
        const body = response.ok ? await readJson(response) : null
        if (body?.status === 'connected' || body?.status === 'failed') {
          stopPolling()
          closePopup()
          if (!mountedRef.current) return
          setPendingId(null)
          if (body.status === 'connected') {
            setMessage({ kind: 'success', text: body.message || `${connection.label} connected.` })
            onConnected()
          } else {
            setMessage({ kind: 'error', text: body.message || `${connection.label} was not connected.` })
          }
          return
        }
      } catch (_) { /* network blip: keep polling */ }
      // The user closed the popup: the flow can still finish on the server for a moment.
      if (popupClosed && ++pollsAfterClose > 2) {
        stopPolling()
        popupRef.current = null
        if (mountedRef.current) setPendingId(null)
      }
    }, POLL_INTERVAL_MS)
  }, [stopPolling, closePopup, onConnected])

  return { connect, pendingId, message, setMessage }
}

function StatusBadge({ status }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[status] || STATUS_CLASS.not_connected}`}>
      {STATUS_TEXT[status] || status}
    </span>
  )
}

function ConnectionRow({ connection, highlighted, pending, onConnect }) {
  const rowRef = useRef(null)
  useEffect(() => {
    if (highlighted && rowRef.current?.scrollIntoView) rowRef.current.scrollIntoView({ block: 'center' })
  }, [highlighted])
  const canConnect = connection.status === 'not_connected' || connection.status === 'reconnect_required'
  return (
    <li
      ref={rowRef}
      data-connection-id={connection.id}
      className={`flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between ${highlighted ? 'bg-primary/5' : ''}`}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{connection.label}</span>
          <StatusBadge status={connection.status} />
        </div>
        <p className="mt-1 text-sm text-muted-foreground">{connection.description}</p>
        {connection.status === 'unavailable' && (
          <p className="mt-1 text-xs text-muted-foreground">This app has not finished setting up {connection.provider_label} yet.</p>
        )}
      </div>
      {canConnect && (
        <button
          type="button"
          onClick={() => onConnect(connection)}
          disabled={pending}
          className="shrink-0 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
        >
          {pending ? 'Connecting…' : connection.status === 'reconnect_required' ? 'Reconnect' : 'Connect'}
        </button>
      )}
    </li>
  )
}

function AccountRow({ account, busy, onDisconnect }) {
  return (
    <li className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{account.label}</span>
          <StatusBadge status={account.status} />
        </div>
        {account.account_email && (
          <p className="mt-1 truncate text-sm text-muted-foreground">Connected as {account.account_email}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => onDisconnect(account)}
        disabled={busy}
        className="shrink-0 rounded-md border border-border px-3 py-1.5 text-sm text-foreground hover:bg-muted disabled:opacity-60"
      >
        {busy ? 'Disconnecting…' : 'Disconnect'}
      </button>
    </li>
  )
}

export default function ConnectedAccountsTab() {
  const location = useLocation()
  const highlightId = new URLSearchParams(location.search).get('connect')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [disconnecting, setDisconnecting] = useState(null)

  const load = useCallback(async () => {
    try {
      const response = await authFetch('/api/me/connected-accounts')
      if (!response.ok) throw new Error(`${response.status}`)
      setData(await response.json())
      setLoadError(null)
    } catch (_) {
      setLoadError('Could not load your connected accounts.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const { connect, pendingId, message, setMessage } = useConnectPopup(load)

  const disconnect = useCallback(async (account) => {
    const confirmed = typeof window === 'undefined' || window.confirm(
      `Disconnect ${account.label}? This app will no longer be able to use your ${account.label} account.`,
    )
    if (!confirmed) return
    setDisconnecting(account.provider)
    try {
      const response = await authFetch(
        `/api/me/connected-accounts/providers/${encodeURIComponent(account.provider)}`,
        { method: 'DELETE' },
      )
      if (!response.ok) throw new Error(`${response.status}`)
      setMessage({ kind: 'success', text: `${account.label} disconnected.` })
    } catch (_) {
      setMessage({ kind: 'error', text: `Could not disconnect ${account.label}. Try again.` })
    } finally {
      setDisconnecting(null)
      load()
    }
  }, [load, setMessage])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }
  if (loadError) {
    return <p className="text-sm text-destructive">{loadError}</p>
  }

  const connections = Array.isArray(data?.connections) ? data.connections : []
  const accounts = Array.isArray(data?.providers) ? data.providers : []

  return (
    <div className="space-y-6">
      {message && (
        <div
          role="status"
          className={`rounded-xl border px-4 py-3 text-sm ${message.kind === 'error' ? 'border-destructive/30 bg-destructive/10 text-destructive' : 'border-success/30 bg-success/10 text-success'}`}
        >
          {message.text}
        </div>
      )}

      <section>
        <h3 className="text-sm font-semibold text-foreground">Available connections</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Connect your own accounts so this app can work with them for you. You can disconnect at any time.
        </p>
        {connections.length === 0 ? (
          <p className="mt-3 text-sm italic text-muted-foreground">This app does not offer any connections right now.</p>
        ) : (
          <ul className="mt-3 divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
            {connections.map((connection) => (
              <ConnectionRow
                key={connection.id}
                connection={connection}
                highlighted={connection.id === highlightId}
                pending={pendingId === connection.id}
                onConnect={connect}
              />
            ))}
          </ul>
        )}
      </section>

      {accounts.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-foreground">Your connected accounts</h3>
          <ul className="mt-3 divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
            {accounts.map((account) => (
              <AccountRow
                key={account.provider}
                account={account}
                busy={disconnecting === account.provider}
                onDisconnect={disconnect}
              />
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
