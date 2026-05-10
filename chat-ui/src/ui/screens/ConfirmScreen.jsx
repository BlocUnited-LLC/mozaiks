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

import { useCallback } from 'react';

const asObject = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value;
};

const asString = (value) => {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
};

export function ConfirmScreen({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const screenProps = asObject(transition?.ui?.props);
  const title = asString(screenProps.title) ?? 'Confirm this step';
  const confirm_message = asString(screenProps.message) ?? 'Are you sure?';
  const confirm_label = asString(screenProps.confirm_label) ?? 'Confirm';
  const cancel_label = asString(screenProps.cancel_label) ?? 'Cancel';

  const options = transition?.options ?? [];
  const confirmOpt = options.find((o) => o.id === 'confirm') ?? options[0];
  const cancelOpt = options.find((o) => o.id === 'cancel') ?? options[1];

  const hasConfirmRoute = Boolean(confirmOpt?.id || transition?.confirm_route);
  const hasCancelRoute = Boolean(cancelOpt?.id || transition?.cancel_route);

  const handleConfirm = useCallback(() => {
    if (hasConfirmRoute) onResolve?.(confirmOpt?.id ?? 'confirm');
  }, [onResolve, hasConfirmRoute, confirmOpt]);

  const handleCancel = useCallback(() => {
    if (hasCancelRoute) onResolve?.(cancelOpt?.id ?? 'cancel');
  }, [onResolve, hasCancelRoute, cancelOpt]);

  return (
    <div className="p-6 text-center sm:p-8">
      <div className="mx-auto max-w-xl">
        <p className="text-xs font-semibold uppercase tracking-widest text-primary/80">Confirmation</p>
        <h1 id={overlayTitleId} className="mt-3 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
          {title}
        </h1>
        <p id={overlayDescriptionId} className="mt-4 text-sm leading-7 text-muted-foreground sm:text-base">
          {confirm_message}
        </p>

        <div className="mt-8 flex flex-col-reverse gap-3 sm:flex-row sm:justify-center">
          <button
            className="min-h-12 rounded-xl border border-border/70 bg-muted px-5 py-3 text-sm font-semibold text-muted-foreground transition hover:bg-muted/70 focus:outline-none focus:ring-2 focus:ring-border disabled:cursor-not-allowed disabled:opacity-60 sm:min-w-40"
            onClick={handleCancel}
            disabled={!hasCancelRoute}
          >
            {cancel_label}
          </button>
          <button
            className="min-h-12 rounded-xl border border-primary/40 bg-primary px-5 py-3 text-sm font-semibold text-primary-foreground transition hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:cursor-not-allowed disabled:opacity-60 sm:min-w-40"
            onClick={handleConfirm}
            disabled={!hasConfirmRoute}
          >
            {confirm_label}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ConfirmScreen;
