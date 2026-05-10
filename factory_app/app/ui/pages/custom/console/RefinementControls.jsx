import {
  ActionButton,
  IconButton,
  StatusPill,
} from './ConsolePrimitives.jsx'
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
  description = 'Describe the change you want. Mozaiks classifies and routes the refinement automatically; the cards below are optional route hints.',
  helperText = null,
  submitLabel = 'Apply refinement',
  surface = 'card',
  showHeader = true,
  showActions = true,
}) {
  const selectedMode = modes.find((mode) => mode.id === selectedClass || mode.changeClass === selectedClass) || null
  const normalizedRequest = typeof request === 'string' ? request : ''
  const canSubmit = normalizedRequest.trim().length > 0 && !busy && (selectedMode?.available !== false)
  const wrapperClass = surface === 'plain'
    ? 'w-full'
    : 'w-full rounded-3xl border border-border bg-background/70 p-5'

  const handleSubmit = () => {
    if (!canSubmit || typeof onSubmit !== 'function') return
    onSubmit(selectedClass || null)
  }

  const handleSelect = (value) => {
    if (typeof onSelectClass === 'function') onSelectClass(value)
  }

  const handleRequestChange = (event) => {
    if (typeof onRequestChange === 'function') onRequestChange(event.target.value)
  }

  return (
    <div className={wrapperClass}>
      {showHeader && (
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h3 className="text-base font-semibold text-foreground">{title}</h3>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">{description}</p>
          </div>
          {onDismiss && <IconButton onClick={onDismiss} label="Dismiss refinement controls" />}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {modes.map((mode) => {
          const modeId = mode.id || mode.changeClass
          const isSelected = selectedClass === modeId
          const badge = isSelected
            ? { label: 'Selected', tone: mode.isDestructive ? 'destructive' : 'primary' }
            : mode.available === false
            ? { label: 'Missing', tone: 'warning' }
            : mode.available === true
            ? { label: 'Ready', tone: 'success' }
            : null

          return (
            <button
              key={modeId}
              type="button"
              onClick={() => handleSelect(modeId)}
              className={[
                'rounded-3xl border p-4 text-left transition-all duration-150',
                mode.available === false
                  ? 'border-border bg-muted/35 text-muted-foreground'
                  : isSelected && mode.isDestructive
                  ? 'border-destructive/40 bg-destructive/10 text-foreground shadow-sm'
                  : isSelected
                  ? 'border-primary/40 bg-primary/10 text-foreground shadow-sm'
                  : 'border-border bg-card text-foreground hover:border-primary/40 hover:bg-muted/60',
              ].join(' ')}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-2xl border border-border bg-background/70 text-lg leading-none">
                    {mode.icon}
                  </span>
                  <div>
                    <div
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
                    </div>
                    {mode.workflowId && (
                      <div className="mt-1 font-mono text-[11px] text-muted-foreground">{mode.workflowId}</div>
                    )}
                  </div>
                </div>
                {badge && <StatusPill tone={badge.tone}>{badge.label}</StatusPill>}
              </div>
              <p className="mt-3 text-sm leading-6 text-muted-foreground">{mode.description}</p>
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
          className="mt-4 min-h-28 w-full resize-none rounded-2xl border border-border bg-card px-4 py-3 text-sm leading-7 text-foreground placeholder:text-muted-foreground outline-none transition-colors focus:border-primary/60"
        />
      )}

      {helperText && (
        <div className="mt-4 rounded-2xl border border-border bg-background/60 px-4 py-3 text-sm leading-6 text-muted-foreground">
          {helperText}
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-2xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {showActions && (
        <div className="mt-4 flex flex-wrap items-center justify-end gap-3">
          {onDismiss && (
            <ActionButton onClick={onDismiss} variant="secondary">
              Cancel
            </ActionButton>
          )}
          <ActionButton onClick={handleSubmit} disabled={!canSubmit}>
            {busy ? 'Starting...' : submitLabel}
          </ActionButton>
        </div>
      )}
    </div>
  )
}

export default RefinementControls
