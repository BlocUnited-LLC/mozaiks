/**
 * LauncherScreen — default shell renderer for user_choice transitions.
 *
 * Falls back to this when a user_choice transition has no registered custom
 * component. Reads presentation hints from transition.ui.props (all optional).
 *
 * Custom transition components replace this entirely and receive the same
 * { transition, onResolve } props — import TransitionChoicePanel,
 * TransitionChoiceCard, and useTransitionMotion from @mozaiks/chat-ui/platform.
 *
 * transition.ui.props (all optional):
 *   title, subtitle, button
 *   options: { [optionId]: { label, description, image, button, badge, helper, disabled } }
 */

import { useCallback } from 'react';
import { TransitionChoicePanel, TransitionChoiceCard, useTransitionMotion } from '@mozaiks/chat-ui/platform';

const asObject = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return value;
};

const asString = (value) => {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
};

const formatOptionLabel = (id) =>
  String(id || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase())
    .trim();

export function LauncherScreen({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = transition?.options ?? [];
  const screenProps = asObject(transition?.ui?.props);
  const optionPropsById = asObject(screenProps.options);
  const motion = useTransitionMotion();

  const title = asString(screenProps.title) ?? 'Choose Your Path';
  const subtitle = asString(screenProps.subtitle);
  const defaultButton = asString(screenProps.button) ?? 'Continue';

  const handleSelect = useCallback(
    (optionId) => {
      onResolve?.(optionId);
    },
    [onResolve],
  );

  return (
    <TransitionChoicePanel
      eyebrow="Transition"
      title={title}
      subtitle={subtitle}
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={motion.entered}
      prefersReducedMotion={motion.prefersReducedMotion}
    >
      {options.map((option, i) => {
        const optionProps = asObject(optionPropsById?.[option.id]);
        return (
          <TransitionChoiceCard
            key={option.id ?? i}
            optionId={option.id}
            label={asString(optionProps.label) ?? formatOptionLabel(option.id)}
            description={asString(optionProps.description) ?? ''}
            image={asString(optionProps.image)}
            cta={asString(optionProps.button) ?? defaultButton}
            helperText={asString(optionProps.helper) ?? ''}
            badge={asString(optionProps.badge) ?? ''}
            disabled={optionProps.disabled === true}
            onResolve={handleSelect}
            entered={motion.entered}
            prefersReducedMotion={motion.prefersReducedMotion}
            delayMs={110 + i * 70}
          />
        );
      })}
    </TransitionChoicePanel>
  );
}

export default LauncherScreen;
