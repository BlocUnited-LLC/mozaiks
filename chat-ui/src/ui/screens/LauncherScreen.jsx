/**
 * LauncherScreen — default shell renderer for user_choice transitions.
 *
 * This is the default shell renderer for user_choice transitions.
 * It reads route options and presentation props from extension_registry.json.
 *
 * Custom transition components replace this entirely — they receive the same
 * { transition, onResolve } props and can render anything.
 *
 * Props:
 *   transition — full WorkflowTransition object from the registry
 *   onResolve  — (option_id: string) => void
 *                fires routing.transition.resolve; shell executes the routing
 *
 * transition.options fields used:
 *   id
 *
 * transition.ui.props (optional):
 *   title, subtitle, background, button
 *   options: {
 *     [optionId]: { label, description, image, button }
 *   }
 */

import { useCallback, useEffect, useState } from 'react';
import { LauncherCard } from './LauncherCard.jsx';

const formatOptionLabel = (id) =>
  String(id || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase())
    .trim();

const asObject = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value;
};

const asString = (value) => {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
};

function usePrefersReducedMotion() {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }

    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setPrefersReducedMotion(mediaQuery.matches);
    update();

    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', update);
      return () => mediaQuery.removeEventListener('change', update);
    }

    mediaQuery.addListener(update);
    return () => mediaQuery.removeListener(update);
  }, []);

  return prefersReducedMotion;
}

function getEnterStyle(entered, prefersReducedMotion, delayMs = 0, distance = 14) {
  if (prefersReducedMotion) return undefined;

  return {
    opacity: entered ? 1 : 0,
    transform: entered ? 'translateY(0px) scale(1)' : `translateY(${distance}px) scale(0.985)`,
    transitionProperty: 'opacity, transform',
    transitionDuration: '420ms',
    transitionTimingFunction: 'cubic-bezier(0.22, 1, 0.36, 1)',
    transitionDelay: `${delayMs}ms`,
  };
}

export function LauncherScreen({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = transition?.options ?? [];
  const screenProps = asObject(transition?.ui?.props);
  const optionPropsById = asObject(screenProps.options);
  const prefersReducedMotion = usePrefersReducedMotion();
  const [entered, setEntered] = useState(false);

  const title = asString(screenProps.title) ?? 'Choose Your Path';
  const subtitle = asString(screenProps.subtitle);
  const background = asString(screenProps.background);
  const defaultButton = asString(screenProps.button) ?? 'Continue';

  useEffect(() => {
    if (prefersReducedMotion) {
      setEntered(true);
      return undefined;
    }

    const frame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(frame);
  }, [prefersReducedMotion]);

  const handleSelect = useCallback(
    (option) => {
      onResolve?.(option.id);
    },
    [onResolve],
  );

  return (
    <div className="relative overflow-hidden">

      {background && (
        <img
          src={background}
          alt=""
          aria-hidden="true"
          className="absolute inset-0 z-0 h-full w-full object-cover opacity-15 pointer-events-none select-none"
          draggable={false}
        />
      )}

      <div className="relative z-10 p-6 sm:p-8 lg:p-10">

        <div className="mx-auto max-w-3xl text-center" style={getEnterStyle(entered, prefersReducedMotion, 0, 10)}>
          <p className="text-xs font-semibold uppercase tracking-widest text-primary/80">Transition</p>
          <h1 id={overlayTitleId} className="mt-3 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            {title}
          </h1>
          {subtitle && (
            <p id={overlayDescriptionId} className="mt-3 text-sm leading-6 text-muted-foreground sm:text-base">{subtitle}</p>
          )}
        </div>

        <div
          className="mt-8 flex flex-wrap items-stretch justify-center gap-4"
        >
          {options.map((option, i) => {
            const optionProps = asObject(optionPropsById?.[option.id]);
            return (
              <LauncherCard
                key={option.id ?? i}
                plan={asString(optionProps.label) ?? formatOptionLabel(option.id)}
                description={asString(optionProps.description) ?? ''}
                image={asString(optionProps.image)}
                button={asString(optionProps.button) ?? defaultButton}
                helperText={asString(optionProps.helper) ?? ''}
                badge={asString(optionProps.badge) ?? ''}
                disabled={optionProps.disabled === true}
                onClick={() => handleSelect(option)}
                style={getEnterStyle(entered, prefersReducedMotion, 110 + i * 70)}
              />
            );
          })}
        </div>

      </div>
    </div>
  );
}

export default LauncherScreen;
