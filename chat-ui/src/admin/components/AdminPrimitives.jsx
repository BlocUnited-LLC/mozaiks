/**
 * AdminPrimitives — shared UI atoms and data hooks for the admin portal.
 *
 * All admin section pages import from here instead of defining their own
 * primitives. Extension panel rendering is also centralised here.
 */

import { useState, useEffect, useCallback } from 'react'
import { getComponent } from '../../registry/componentRegistry'

// ---------------------------------------------------------------------------
// Runtime base URL
// ---------------------------------------------------------------------------

const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
  'http://localhost:8000'

// ---------------------------------------------------------------------------
// Data hook — fetches from the mozaiksai runtime
// ---------------------------------------------------------------------------

export function useAdminFetch(endpoint, intervalMs = 0) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`)
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      setData(await res.json())
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [endpoint])

  useEffect(() => {
    load()
    if (intervalMs > 0) {
      const id = setInterval(load, intervalMs)
      return () => clearInterval(id)
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

export function SectionFrame({ id = null, title, description, children }) {
  return (
    <section id={id || undefined} className="scroll-mt-28 rounded-lg border border-border bg-card p-5">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {children}
    </section>
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
 * Renders a panel declared in admin config but with no registered React component.
 * Shows description and declarative action buttons.
 */
export function DeclarativeExtensionPanel({ panel }) {
  const actions = Array.isArray(panel?.actions) ? panel.actions : []
  const description = panel?.description || panel?.summary

  return (
    <div className="rounded-lg border border-border bg-background p-4">
      {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      {actions.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {actions.map((action) => {
            const id = typeof action === 'string' ? action : action?.id
            const label = typeof action === 'object' ? action?.label || id : id
            if (!id) return null
            return (
              <button
                key={id}
                type="button"
                className="rounded-md border border-border px-3 py-1.5 text-xs text-foreground hover:bg-muted"
              >
                {label}
              </button>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}

function getPanelId(panelConfig) {
  return typeof panelConfig === 'string' ? panelConfig : panelConfig?.id
}

/**
 * Renders a list of extension panels. Each panel is resolved from the
 * component registry by id; falls back to DeclarativeExtensionPanel.
 */
export function AdminExtensionPanels({ panels }) {
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
            {Custom ? <Custom panel={panelConfig} /> : <DeclarativeExtensionPanel panel={panelConfig} />}
          </div>
        )
      })}
    </>
  )
}
