/**
 * StudioPrimitives — shared UI atoms for Studio pages.
 */

export const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) || ''

export function StatusPill({ children, tone = 'default' }) {
  const tones = {
    default: 'border-border bg-muted text-muted-foreground',
    primary: 'border-primary/30 bg-primary/10 text-primary',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
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

export function StudioLoadingState({ label = 'Loading…' }) {
  return (
    <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10">
      <div className="flex items-center gap-3 rounded-2xl border border-border bg-card px-5 py-4 text-sm text-muted-foreground shadow-sm">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        {label}
      </div>
    </div>
  )
}

export function StudioErrorState({ title = 'Unavailable', message }) {
  return (
    <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10">
      <div className="max-w-xl rounded-3xl border border-destructive/30 bg-destructive/10 p-6 shadow-sm">
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-destructive">{title}</div>
        <p className="mt-3 text-sm leading-6 text-foreground">{message}</p>
      </div>
    </div>
  )
}
