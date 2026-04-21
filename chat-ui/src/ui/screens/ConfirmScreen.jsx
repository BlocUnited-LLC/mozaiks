/**
 * ConfirmScreen — built-in renderer for "confirm" transition type.
 *
 * Registered as "ConfirmScreen" in the component registry.
 * The shell mounts this for any confirm transition with no custom component.
 *
 * Props (injected by TransitionScreen):
 *   transition — WorkflowTransition object from extension_registry.json
 *   onResolve — (option_id: string) => void
 *
 * transition.options:
 *   options[0] with id "confirm" → confirm route_to
 *   options[1] with id "cancel"  → cancel route_to
 *   Falls back to transition.confirm_route / transition.cancel_route.
 */

import { useCallback } from 'react'

const asObject = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value
}

const asString = (value) => {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function ConfirmScreen({ transition, onResolve }) {
  const screenProps = asObject(transition?.ui?.props)
  const title = asString(screenProps.title)
  const confirm_message = asString(screenProps.message) ?? 'Are you sure?'
  const confirm_label = asString(screenProps.confirm_label) ?? 'Confirm'
  const cancel_label = asString(screenProps.cancel_label) ?? 'Cancel'

  const options = transition?.options ?? []
  const confirmOpt = options.find((o) => o.id === 'confirm') ?? options[0]
  const cancelOpt = options.find((o) => o.id === 'cancel') ?? options[1]

  const hasConfirmRoute = Boolean(confirmOpt?.id || transition?.confirm_route)
  const hasCancelRoute = Boolean(cancelOpt?.id || transition?.cancel_route)

  const handleConfirm = useCallback(() => {
    if (hasConfirmRoute) onResolve?.(confirmOpt?.id ?? 'confirm')
  }, [onResolve, hasConfirmRoute, confirmOpt])

  const handleCancel = useCallback(() => {
    if (hasCancelRoute) onResolve?.(cancelOpt?.id ?? 'cancel')
  }, [onResolve, hasCancelRoute, cancelOpt])

  return (
    <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-12">
      <div className="rounded-2xl border border-border bg-card p-10 text-center max-w-sm w-full shadow-lg">

        {title && (
          <h1 className="text-xl font-bold text-foreground mb-4">{title}</h1>
        )}

        <p className="text-base text-muted-foreground mb-8 leading-relaxed">
          {confirm_message}
        </p>

        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <button
            className="flex-1 px-6 py-3 rounded-xl bg-primary text-primary-foreground font-semibold hover:bg-primary/90 transition-colors focus:outline-none focus:ring-2 focus:ring-primary/50"
            onClick={handleConfirm}
            disabled={!hasConfirmRoute}
          >
            {confirm_label}
          </button>
          <button
            className="flex-1 px-6 py-3 rounded-xl bg-muted text-muted-foreground font-semibold hover:bg-muted/70 transition-colors focus:outline-none focus:ring-2 focus:ring-border"
            onClick={handleCancel}
            disabled={!hasCancelRoute}
          >
            {cancel_label}
          </button>
        </div>

      </div>
    </div>
  )
}

export default ConfirmScreen
