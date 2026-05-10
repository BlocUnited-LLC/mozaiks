import React from 'react'

const TITLES = {
  workflow_reentry: 'Workflow Re-Entry',
  core_restart: 'Core Change Detected',
  auto_patch: 'Scoped Patch',
  clarify_scope: 'Clarify Scope',
  fallback_workflow: 'Workflow Fallback',
}

export default function HarnessDecisionCard({
  decision,
  busy = false,
  error = null,
  onAction,
  className = '',
}) {
  if (!decision || typeof decision !== 'object') return null

  const actions = Array.isArray(decision.actions) ? decision.actions : []
  const selectedPaths = Array.isArray(decision.selected_paths) ? decision.selected_paths : []
  const title = TITLES[decision.decision_type] || 'Routing Decision'

  return (
    <div className={['rounded-2xl border border-border bg-background/70 p-4', className].join(' ').trim()}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Build Routing</div>
          <div className="mt-1 text-base font-semibold text-foreground">{title}</div>
        </div>
        {typeof decision.confidence === 'number' && (
          <div className="rounded-xl border border-border bg-background/70 px-3 py-1 text-xs text-muted-foreground">
            Confidence {Math.round(decision.confidence * 100)}%
          </div>
        )}
      </div>

      <p className="mt-3 text-sm font-medium text-foreground">{decision.message}</p>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">{decision.rationale}</p>

      {decision.clarification_question && (
        <div className="mt-3 rounded-xl border border-primary/25 bg-primary/5 px-3 py-2 text-sm text-foreground">
          {decision.clarification_question}
        </div>
      )}

      {selectedPaths.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {selectedPaths.map((path) => (
            <span key={path} className="rounded-lg border border-border bg-muted/35 px-2 py-1 font-mono text-[11px] text-muted-foreground">
              {path}
            </span>
          ))}
        </div>
      )}

      {actions.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-3">
          {actions.map((action) => (
            <button
              key={action.action_id}
              type="button"
              disabled={busy}
              onClick={() => typeof onAction === 'function' && onAction(action)}
              className="rounded-xl border border-primary/25 bg-primary/10 px-4 py-2 text-sm font-semibold text-foreground transition hover:bg-primary/15 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy ? 'Working...' : action.label}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="mt-3 rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}
    </div>
  )
}
