/**
 * AdminConnectedAccountsPanel — the app's admins choose which account
 * connections the app offers its users (Google Calendar, Gmail, ...).
 *
 * Shown on the admin Settings page when an installed plugin declares an
 * account connection. Reads GET /api/admin/connected-accounts and writes
 * PUT /api/admin/connected-accounts/{id} ({ offered }). Admins see whether
 * the app's OAuth client is configured (setting names only, never values),
 * the redirect URI to register with the provider, and how many users are
 * connected. They never see a user's account or token.
 */

import { useState, useEffect, useCallback } from 'react'
import { authFetch } from '../../adapters/api.js'
import { Badge, ErrorBox, SectionHeading } from './AdminPrimitives.jsx'

function ConnectionAdminRow({ connection, counts, saving, onToggle }) {
  const configured = connection.client_configured
  return (
    <li className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{connection.label}</span>
          <Badge variant={connection.offered ? (configured ? 'success' : 'warning') : 'default'}>
            {connection.offered ? (configured ? 'Offered' : 'Setup needed') : 'Not offered'}
          </Badge>
          <span className="text-xs text-muted-foreground">from {connection.module_id}</span>
        </div>
        <p className="text-sm text-muted-foreground">{connection.description}</p>
        {!configured && Array.isArray(connection.client_settings) && (
          <p className="text-xs text-muted-foreground">
            Configure the app's {connection.provider_label} OAuth client:{' '}
            {connection.client_settings.map((name, index) => (
              <span key={name}>
                {index > 0 ? ' and ' : ''}
                <code className="font-mono">{name}</code>
              </span>
            ))}
            .
          </p>
        )}
        {counts && (
          <p className="text-xs text-muted-foreground">
            {counts.connected} user{counts.connected === 1 ? '' : 's'} connected to {connection.provider_label}
            {counts.needs_reconnect ? `, ${counts.needs_reconnect} need to reconnect` : ''}
          </p>
        )}
      </div>
      <label className="flex shrink-0 cursor-pointer items-center gap-2 text-sm text-foreground">
        <input
          type="checkbox"
          checked={Boolean(connection.offered)}
          disabled={saving}
          onChange={(event) => onToggle(connection, event.target.checked)}
          className="h-4 w-4 accent-primary"
        />
        Offer to users
      </label>
    </li>
  )
}

export default function AdminConnectedAccountsPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(null)

  const load = useCallback(async () => {
    try {
      const response = await authFetch('/api/admin/connected-accounts')
      // 404: this app host has no connected accounts (no app loaded yet).
      if (response.status === 404) { setData(null); setError(null); return }
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
      setData(await response.json())
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const toggle = useCallback(async (connection, offered) => {
    setSaving(connection.id)
    // Show the admin's choice at once; the reload below confirms it or puts it back.
    setData((prev) => prev && {
      ...prev,
      connections: prev.connections.map((row) => (row.id === connection.id ? { ...row, offered } : row)),
    })
    try {
      const response = await authFetch(`/api/admin/connected-accounts/${encodeURIComponent(connection.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ offered }),
      })
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
      setError(null)
    } catch (e) {
      setError(`Could not update ${connection.label}: ${e.message}`)
    } finally {
      setSaving(null)
      load()
    }
  }, [load])

  const connections = Array.isArray(data?.connections) ? data.connections : []
  if (!error && connections.length === 0) return null
  const counts = Object.fromEntries(
    (Array.isArray(data?.providers) ? data.providers : []).map((row) => [row.provider, row]),
  )

  return (
    <div>
      <SectionHeading>Connected accounts</SectionHeading>
      <p className="mb-3 text-sm text-muted-foreground">
        Plugins that work with your users' own accounts. Each user decides whether to connect; you decide which
        connections this app offers.
      </p>
      {error && <ErrorBox message={`Connected accounts: ${error}`} />}
      {data?.config_error && <ErrorBox message={`A plugin's connection declaration is invalid: ${data.config_error}`} />}
      {connections.length > 0 && (
        <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-background">
          {connections.map((connection) => (
            <ConnectionAdminRow
              key={connection.id}
              connection={connection}
              counts={counts[connection.provider]}
              saving={saving === connection.id}
              onToggle={toggle}
            />
          ))}
        </ul>
      )}
      {data?.redirect_uri && (
        <p className="mt-3 text-xs text-muted-foreground">
          Redirect URI to register with each provider:{' '}
          <code className="break-all font-mono">{data.redirect_uri}</code>
        </p>
      )}
    </div>
  )
}
