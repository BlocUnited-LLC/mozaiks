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
 * transition.ui.props (all optional):
 *   title, message, confirm_label, cancel_label, icon
 *
 * transition.options:
 *   options[0] with id "confirm" → confirm route_to
 *   options[1] with id "cancel"  → cancel route_to
 *   Falls back to transition.confirm_route / transition.cancel_route.
 *
 * For custom confirm-style screens import TransitionActionPanel and
 * TransitionActionButton from @mozaiks/chat-ui/platform.
 */

import { useCallback } from 'react';
import { TransitionActionPanel, TransitionActionButton, useTransitionMotion } from '@mozaiks/chat-ui/platform';

const asObject = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
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
  const icon = asString(screenProps.icon) ?? 'info';

  const options = transition?.options ?? [];
  const confirmOpt = options.find((o) => o.id === 'confirm') ?? options[0];
  const cancelOpt = options.find((o) => o.id === 'cancel') ?? options[1];

  const hasConfirmRoute = Boolean(confirmOpt?.id || transition?.confirm_route);
  const hasCancelRoute = Boolean(cancelOpt?.id || transition?.cancel_route);

  const { entered, prefersReducedMotion } = useTransitionMotion();

  const handleConfirm = useCallback(() => {
    if (hasConfirmRoute) onResolve?.(confirmOpt?.id ?? 'confirm');
  }, [onResolve, hasConfirmRoute, confirmOpt]);

  const handleCancel = useCallback(() => {
    if (hasCancelRoute) onResolve?.(cancelOpt?.id ?? 'cancel');
  }, [onResolve, hasCancelRoute, cancelOpt]);

  return (
    <TransitionActionPanel
      eyebrow="Confirmation"
      title={title}
      body={confirm_message}
      icon={icon}
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={entered}
      prefersReducedMotion={prefersReducedMotion}
    >
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-center">
        <TransitionActionButton
          label={cancel_label}
          variant="secondary"
          onClick={handleCancel}
          disabled={!hasCancelRoute}
        />
        <TransitionActionButton
          label={confirm_label}
          variant="primary"
          onClick={handleConfirm}
          disabled={!hasConfirmRoute}
        />
      </div>
    </TransitionActionPanel>
  );
}

export default ConfirmScreen;
