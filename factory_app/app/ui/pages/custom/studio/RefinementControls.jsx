import {
  getRefinementRequestPlaceholder,
  REFINEMENT_CHANGE_CLASSES,
} from './refinement.js'


export function RefinementControls({
  modes = REFINEMENT_CHANGE_CLASSES,
  selectedClass = null,
  onSelectClass,
  request = '',
  onRequestChange,
  onSubmit,
  onDismiss,
  busy = false,
  error = null,
  showRequestInput = true,
  title = 'Refine this artifact',
  description = 'Choose how much to change, then describe what you want.',
  helperText = null,
  submitLabel = 'Apply refinement',
}) {
  const selectedMode = modes.find((mode) => mode.id === selectedClass || mode.changeClass === selectedClass) || null
  const normalizedRequest = typeof request === 'string' ? request : ''
  const canSubmit = Boolean(selectedClass) && normalizedRequest.trim().length > 0 && !busy && selectedMode?.available !== false

  const handleSubmit = () => {
    if (!canSubmit || typeof onSubmit !== 'function') return
    onSubmit(selectedClass)
  }

  const handleSelect = (value) => {
    if (typeof onSelectClass === 'function') onSelectClass(value)
  }

  const handleRequestChange = (event) => {
    if (typeof onRequestChange === 'function') onRequestChange(event.target.value)
  }

  return (
    <div className="w-full rounded-3xl border border-border bg-background/70 p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-foreground">{title}</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            {description}
          </p>
        </div>
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="text-muted-foreground hover:text-foreground transition-colors p-1"
            aria-label="Dismiss"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 mb-4">
        {modes.map((mode) => {
          const modeId = mode.id || mode.changeClass
          const isSelected = selectedClass === modeId
          return (
            <button
              key={modeId}
              onClick={() => handleSelect(modeId)}
              className={[
                'text-left p-3 rounded-lg border transition-all duration-150',
                mode.available === false
                  ? 'border-border bg-muted/50 text-muted-foreground'
                  : isSelected && !mode.isDestructive
                  ? 'border-primary bg-primary/10 text-foreground'
                  : isSelected && mode.isDestructive
                  ? 'border-destructive bg-destructive/10 text-foreground'
                  : 'border-border bg-card text-foreground hover:border-primary/50 hover:bg-muted',
              ].join(' ')}
            >
              <div className="flex items-center justify-between gap-2 mb-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-base leading-none">{mode.icon}</span>
                  <span
                    className={[
                      'text-sm font-semibold',
                      mode.available === false
                        ? 'text-muted-foreground'
                        : isSelected && mode.isDestructive
                        ? 'text-destructive'
                        : isSelected
                        ? 'text-primary'
                        : 'text-foreground',
                    ].join(' ')}
                  >
                    {mode.label || mode.title}
                  </span>
                </div>
                {mode.available === false ? (
                  <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-warning">Missing</span>
                ) : mode.available === true ? (
                  <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-success">Ready</span>
                ) : null}
              </div>
              {mode.workflowId && (
                <div className="mb-1 text-[11px] font-mono text-muted-foreground">{mode.workflowId}</div>
              )}
              <p className="text-xs text-muted-foreground leading-snug">{mode.description}</p>
            </button>
          )
        })}
      </div>

      {showRequestInput && (
        <textarea
          value={normalizedRequest}
          onChange={handleRequestChange}
          placeholder={getRefinementRequestPlaceholder(selectedClass)}
          rows={3}
          className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground resize-none focus:outline-none focus:border-primary/60 transition-colors mb-3"
        />
      )}

      {helperText && (
        <div className="mb-3 text-sm text-muted-foreground leading-6">{helperText}</div>
      )}

      {error && (
        <div className="mb-3 px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/30 text-destructive text-xs">
          {error}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground border border-border rounded-lg hover:border-primary/40 transition-colors"
          >
            Cancel
          </button>
        )}
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className={[
            'px-4 py-1.5 text-sm font-medium rounded-lg transition-all duration-150',
            canSubmit
              ? 'bg-primary text-primary-foreground hover:bg-primary/90'
              : 'bg-muted text-muted-foreground cursor-not-allowed',
          ].join(' ')}
        >
          {busy ? 'Starting...' : submitLabel}
        </button>
      </div>
    </div>
  )
}

export default RefinementControls