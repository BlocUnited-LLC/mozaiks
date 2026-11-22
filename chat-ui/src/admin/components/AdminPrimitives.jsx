/**
 * AdminPrimitives — shared UI atoms and data hooks for the admin portal.
 *
 * All admin section pages import from here instead of defining their own
 * primitives. Module panel rendering is also centralised here.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { getComponent } from '../../registry/componentRegistry'
import { useChatUI } from '../../context/ChatUIContext'
import { authFetch } from '../../adapters/api.js'
import AdminSchemaPanel from './AdminSchemaPanel.jsx'

// ---------------------------------------------------------------------------
// Runtime base URL
// ---------------------------------------------------------------------------

const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
  ''

// ---------------------------------------------------------------------------
// Data hook — fetches from the mozaiksai runtime
// ---------------------------------------------------------------------------

export function useAdminFetch(endpoint, intervalMs = 0) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const currentRequest = useRef(null)

  const load = useCallback(async () => {
    currentRequest.current?.abort()
    const controller = new AbortController()
    currentRequest.current = controller
    const isCurrent = () => currentRequest.current === controller && !controller.signal.aborted
    setLoading(true)
    setError(null)
    try {
      // authFetch owns backend URL resolution and the current session token.
      const res = await authFetch(endpoint, { signal: controller.signal })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const result = await res.json()
      if (isCurrent()) setData(result)
    } catch (e) {
      if (isCurrent()) {
        setData(null)
        setError(e.message)
      }
    } finally {
      if (isCurrent()) setLoading(false)
    }
  }, [endpoint])

  useEffect(() => {
    setData(null)
    load()
    const id = intervalMs > 0 ? setInterval(load, intervalMs) : null
    return () => {
      if (id !== null) clearInterval(id)
      currentRequest.current?.abort()
      currentRequest.current = null
    }
  }, [load, intervalMs])

  return { data, loading, error, refresh: load }
}

// ---------------------------------------------------------------------------
// Atoms
// ---------------------------------------------------------------------------

export function StatCard({ label, value, sub, accent = false }) {
  return (
    <div className={`rounded-xl border p-5 flex flex-col gap-1 ${accent ? 'border-primary/40 bg-primary/10' : 'border-border bg-card'}`}>
      <span className="text-xs text-muted-foreground uppercase tracking-wide">{label}</span>
      <span className={`text-3xl font-bold ${accent ? 'text-primary' : 'text-foreground'}`}>
        {value ?? '—'}
      </span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </div>
  )
}

export function SectionHeading({ children }) {
  return (
    <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-widest mt-6 mb-3">
      {children}
    </h2>
  )
}

export function Badge({ children, variant = 'default' }) {
  const styles = {
    default: 'bg-muted text-muted-foreground',
    success: 'bg-success/20 text-success',
    warning: 'bg-warning/20 text-warning',
    error:   'bg-destructive/20 text-destructive',
    primary: 'bg-primary/20 text-primary',
  }
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${styles[variant]}`}>
      {children}
    </span>
  )
}

export function ErrorBox({ message }) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
      {message}
    </div>
  )
}

export function EmptyState({ children }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-background p-5 text-sm text-muted-foreground">
      {children}
    </div>
  )
}

export function Spinner() {
  return (
    <div className="flex items-center gap-2 text-muted-foreground text-sm">
      <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading…
    </div>
  )
}

// ---------------------------------------------------------------------------
// Extension panel renderers
// ---------------------------------------------------------------------------

/**
 * Shown in place of a custom panel whose React component is not registered in
 * this app. A panel with no component has nothing to render, so say so rather
 * than draw an empty box.
 */
export function MissingPanelComponent({ componentName }) {
  return (
    <EmptyState>
      This panel cannot be shown because its component
      {componentName ? <> <code className="font-mono text-xs">{componentName}</code></> : null} is
      not installed in this app.
    </EmptyState>
  )
}

function getPanelId(panelConfig) {
  return typeof panelConfig === 'string' ? panelConfig : panelConfig?.id
}

/**
 * Renders module panels (`modules/{module}/contracts/admin.yaml`). A schema
 * panel renders its sections; a custom panel renders the registered component
 * named by `component`.
 */
export function AdminExtensionPanels({ panels }) {
  const { auth } = useChatUI()
  if (!panels?.length) return null

  return (
    <>
      {panels.map((panelConfig) => {
        const panelId = getPanelId(panelConfig)
        if (!panelId) return null
        const label = typeof panelConfig === 'object' && panelConfig?.label ? panelConfig.label : panelId
        const componentName =
          typeof panelConfig === 'object' && panelConfig?.component ? panelConfig.component : panelId
        const Custom = getComponent(componentName)

        return (
          <div key={panelId}>
            <SectionHeading>{label}</SectionHeading>
            {panelConfig?.renderer === 'schema' ? (
              <AdminSchemaPanel panel={panelConfig} />
            ) : Custom ? (
              <Custom panel={panelConfig} apiBaseUrl={API_BASE} auth={auth} />
            ) : (
              <MissingPanelComponent componentName={componentName} />
            )}
          </div>
        )
      })}
    </>
  )
}
