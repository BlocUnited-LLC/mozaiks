export const workflowSurfaceStyles = {
  panel: 'rounded-2xl border border-border bg-card p-0 overflow-hidden',
  primaryPanel: 'rounded-2xl border border-primary bg-card p-0 overflow-hidden',
  darkPanel: 'rounded-2xl border border-border bg-card p-0 overflow-hidden',
  assistiveText: 'text-xs font-sans text-muted-foreground',
  errorText: 'text-xs font-sans text-destructive',
  buttonGroup: 'flex items-center gap-3',
  secondaryButton:
    'rounded-lg border border-border bg-card px-4 py-2 text-sm font-sans text-muted-foreground transition hover:border-border hover:bg-muted disabled:opacity-60 flex-1',
  primaryButton:
    'rounded-lg bg-primary px-6 py-3 text-xs font-sans font-bold uppercase tracking-wide text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-60 flex-1',
  skipButton:
    'rounded-lg border border-border bg-card px-6 py-3 text-xs font-sans font-bold uppercase tracking-wide text-muted-foreground transition-all hover:bg-muted disabled:opacity-60 flex-1',
  toolbarButtonBase:
    'px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors inline-flex items-center gap-2',
  toolbarButtonActive:
    'bg-[rgba(var(--color-primary-rgb),0.25)] border border-[rgba(var(--color-primary-rgb),0.35)] text-white',
  toolbarButtonInactive:
    'bg-white/5 hover:bg-white/10 border border-white/10 text-[var(--color-text-secondary)]',
}

export function workflowToolbarButtonClass(active) {
  return [
    workflowSurfaceStyles.toolbarButtonBase,
    active ? workflowSurfaceStyles.toolbarButtonActive : workflowSurfaceStyles.toolbarButtonInactive,
  ].join(' ')
}
