import { useEffect, useState } from 'react'
import { RiCloseLine } from 'react-icons/ri'

/**
 * ConsolePrimitives — shared UI atoms for first-party workspace pages.
 */

export const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) || ''

export function StatusPill({ children, tone = 'default' }) {
  const tones = {
    default: 'border-border bg-muted text-muted-foreground',
    primary: 'border-primary/30 bg-primary/10 text-primary',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
    destructive: 'border-destructive/30 bg-destructive/10 text-destructive',
  }
  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] ${tones[tone]}`}>
      {children}
    </span>
  )
}

export function SurfaceCard({ title, eyebrow, children, accent = false, className = '' }) {
  return (
    <section
      className={`rounded-3xl border p-6 shadow-sm ${accent ? 'border-primary/30 bg-gradient-to-br from-primary/10 via-card to-secondary/10' : 'border-border bg-card'} ${className}`}
    >
      {eyebrow && (
        <div className="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-muted-foreground">{eyebrow}</div>
      )}
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  )
}

export function Metric({ label, value, detail = null }) {
  return (
    <div className="rounded-2xl border border-border bg-background/70 p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-foreground">{value}</div>
      {detail && <div className="mt-1 text-sm text-muted-foreground">{detail}</div>}
    </div>
  )
}

export function ActionButton({ children, onClick, disabled = false, variant = 'primary' }) {
  const variants = {
    primary: 'bg-primary text-primary-foreground hover:bg-primary/90 border-primary/30',
    secondary: 'bg-background text-foreground hover:bg-muted border-border',
    destructive: 'bg-destructive/10 text-destructive hover:bg-destructive/20 border-destructive/30',
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center rounded-2xl border px-4 py-2.5 text-sm font-semibold transition ${variants[variant]} disabled:cursor-not-allowed disabled:opacity-50`}
    >
      {children}
    </button>
  )
}

export function IconButton({ onClick, label, disabled = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      disabled={disabled}
      className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border/70 bg-muted/30 text-muted-foreground transition hover:border-border hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
    >
      <RiCloseLine className="h-5 w-5" aria-hidden="true" />
    </button>
  )
}

function readShellChromeInsets() {
  if (typeof document === 'undefined') {
    return { top: 0, bottom: 0 }
  }

  const shellHeader = document.querySelector('header')
  const shellFooter = document.querySelector('.shell-footer')

  return {
    top: Math.max(0, Math.ceil(shellHeader?.getBoundingClientRect().height || 0)),
    bottom: Math.max(0, Math.ceil(shellFooter?.getBoundingClientRect().height || 0)),
  }
}

function useShellChromeInsets(active) {
  const [insets, setInsets] = useState(() => readShellChromeInsets())

  useEffect(() => {
    if (!active) return undefined

    const updateInsets = () => setInsets(readShellChromeInsets())
    updateInsets()

    if (typeof window !== 'undefined') {
      window.addEventListener('resize', updateInsets)
    }

    let observer = null
    if (typeof ResizeObserver !== 'undefined' && typeof document !== 'undefined') {
      observer = new ResizeObserver(updateInsets)
      const shellHeader = document.querySelector('header')
      const shellFooter = document.querySelector('.shell-footer')
      if (shellHeader) observer.observe(shellHeader)
      if (shellFooter) observer.observe(shellFooter)
    }

    return () => {
      if (typeof window !== 'undefined') {
        window.removeEventListener('resize', updateInsets)
      }
      observer?.disconnect()
    }
  }, [active])

  return insets
}

export function ConsoleSlideOver({
  open,
  title,
  description = null,
  onClose,
  children,
  footer = null,
  maxWidthClass = 'max-w-xl',
}) {
  const { top, bottom } = useShellChromeInsets(open)

  useEffect(() => {
    if (!open) return undefined

    function handleKeyDown(event) {
      if (event.key === 'Escape') onClose()
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  useEffect(() => {
    if (!open || typeof document === 'undefined') return undefined

    const floatingRoots = [
      document.getElementById('mozaiks-embed-root'),
      ...Array.from(document.querySelectorAll('.widget-safe-bottom')),
    ].filter(Boolean)

    const previousState = floatingRoots.map((node) => ({
      node,
      visibility: node.style.visibility,
      pointerEvents: node.style.pointerEvents,
      ariaHidden: node.getAttribute('aria-hidden'),
    }))

    previousState.forEach(({ node }) => {
      node.style.visibility = 'hidden'
      node.style.pointerEvents = 'none'
      node.setAttribute('aria-hidden', 'true')
    })

    return () => {
      previousState.forEach(({ node, visibility, pointerEvents, ariaHidden }) => {
        node.style.visibility = visibility
        node.style.pointerEvents = pointerEvents
        if (ariaHidden == null) {
          node.removeAttribute('aria-hidden')
        } else {
          node.setAttribute('aria-hidden', ariaHidden)
        }
      })
    }
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-x-0 z-[100000]"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      style={{
        top: `calc(${top}px + 0.75rem)`,
        bottom: `calc(${bottom}px + 0.75rem)`,
      }}
    >
      <div className="relative flex h-full justify-end px-4 md:px-6">
        <button
          type="button"
          className="absolute inset-0"
          aria-label="Close panel"
          onClick={onClose}
          style={{
            backgroundColor: 'rgba(2, 6, 23, 0.62)',
            WebkitBackdropFilter: 'blur(12px)',
            backdropFilter: 'blur(12px)',
          }}
        />
        <section
          className={`relative flex h-full w-full ${maxWidthClass} flex-col overflow-hidden rounded-3xl border border-border bg-card shadow-2xl`}
        >
          <div className="flex items-start justify-between gap-4 border-b border-border px-6 pb-4 pt-6">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.22em] text-muted-foreground">Mozaiks</div>
              <h2 className="mt-2 text-xl font-semibold text-foreground">{title}</h2>
              {description && <p className="mt-2 text-sm leading-7 text-muted-foreground">{description}</p>}
            </div>
            <IconButton onClick={onClose} label="Close overlay" />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 pt-6">{children}</div>
          {footer && (
            <div
              className="border-t border-border bg-background/80 backdrop-blur-sm"
              style={{ padding: '1.25rem 1.5rem' }}
            >
              {footer}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

export function ConsoleLoadingState({ label = 'Loading…' }) {
  return (
    <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10">
      <div className="flex items-center gap-3 rounded-2xl border border-border bg-card px-5 py-4 text-sm text-muted-foreground shadow-sm">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        {label}
      </div>
    </div>
  )
}

export function ConsoleErrorState({ title = 'Unavailable', message }) {
  return (
    <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10">
      <div className="max-w-xl rounded-3xl border border-destructive/30 bg-destructive/10 p-6 shadow-sm">
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-destructive">{title}</div>
        <p className="mt-3 text-sm leading-6 text-foreground">{message}</p>
      </div>
    </div>
  )
}
