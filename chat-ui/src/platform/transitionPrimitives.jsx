/**
 * Transition UI primitives.
 *
 * Export path: @mozaiks/chat-ui/platform
 *
 * All transition components registered in extension_registry.json should import
 * from here rather than building their own layout, motion, or button systems.
 *
 * Available primitives:
 *   useTransitionMotion         — entry animation hook (entered, prefersReducedMotion)
 *   TransitionChoicePanel       — layout wrapper for user_choice screens
 *   TransitionChoiceCard        — selectable option card for user_choice screens
 *   TransitionActionPanel       — layout wrapper for completion, confirm, and info screens
 *   TransitionActionButton      — styled action button for use inside TransitionActionPanel
 *
 * Canonical component contract (every registered transition component receives):
 *   { transition, onResolve, overlayTitleId, overlayDescriptionId }
 *   transition.context holds merged runtime context variables (workflowName, summary, etc.)
 */

import { useEffect, useState } from 'react';

const ENTRY_EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';

// ---------------------------------------------------------------------------
// useTransitionMotion — shared entry animation hook
// ---------------------------------------------------------------------------

/**
 * Returns { entered, prefersReducedMotion } for consistent entry animation across
 * all transition components. Call at the top of every custom transition component
 * and pass the values down to TransitionChoicePanel, TransitionChoiceCard,
 * TransitionActionPanel, and TransitionActionButton.
 */
export function useTransitionMotion() {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const [entered, setEntered] = useState(false);

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

  useEffect(() => {
    if (prefersReducedMotion) {
      setEntered(true);
      return undefined;
    }

    const frame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(frame);
  }, [prefersReducedMotion]);

  return { entered, prefersReducedMotion };
}

// Alias kept so existing transition components that import useTransitionChoiceMotion
// continue to work without modification.
export const useTransitionChoiceMotion = useTransitionMotion;

function getEnterStyle(entered, prefersReducedMotion, delayMs = 0, distance = 18) {
  if (prefersReducedMotion) return undefined;

  return {
    opacity: entered ? 1 : 0,
    transform: entered ? 'translateY(0px) scale(1)' : `translateY(${distance}px) scale(0.985)`,
    transitionProperty: 'opacity, transform, box-shadow, border-color, background-color',
    transitionDuration: '420ms',
    transitionTimingFunction: ENTRY_EASE,
    transitionDelay: `${delayMs}ms`,
  };
}

// ---------------------------------------------------------------------------
// TransitionChoicePanel — layout wrapper for user_choice screens
// ---------------------------------------------------------------------------

/**
 * Full-height layout wrapper for user_choice transition components.
 * Provides the eyebrow/title/subtitle header, ambient glow blobs, and the
 * flex card grid. Wrap TransitionChoiceCard elements as children.
 */
export function TransitionChoicePanel({
  overlayTitleId,
  overlayDescriptionId,
  eyebrow = 'Transition',
  title,
  subtitle,
  entered,
  prefersReducedMotion,
  children,
}) {
  return (
    <div className="relative min-h-full flex-1 overflow-hidden">
      <div aria-hidden="true" className="pointer-events-none absolute -left-16 top-0 h-44 w-44 rounded-full bg-primary/10 blur-3xl" />
      <div aria-hidden="true" className="pointer-events-none absolute right-0 top-10 h-52 w-52 rounded-full bg-secondary/10 blur-3xl" />

      <div className="relative p-5 sm:p-6 lg:p-8">
        <header
          className="mx-auto max-w-3xl text-center"
          style={getEnterStyle(entered, prefersReducedMotion, 0, 12)}
        >
          <p className="text-xs font-semibold uppercase tracking-widest text-primary/80">{eyebrow}</p>
          <h1 id={overlayTitleId} className="mt-3 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
            {title}
          </h1>
          {subtitle ? (
            <p id={overlayDescriptionId} className="mt-3 text-sm leading-6 text-muted-foreground sm:text-base">
              {subtitle}
            </p>
          ) : null}
        </header>

        <div
          className="mt-6 flex flex-wrap justify-center gap-4 lg:gap-5"
          style={{ alignItems: 'stretch' }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TransitionChoiceCard — selectable option card for user_choice screens
// ---------------------------------------------------------------------------

/**
 * Selectable option card. Image, label, description, CTA button, badge, staggered
 * entry animation. Use inside TransitionChoicePanel.
 *
 * onResolve is called with optionId — matches transition.options[].id.
 * Use delayMs to stagger cards: 120 + index * 80 is a natural cascade.
 */
export function TransitionChoiceCard({
  optionId,
  label,
  description,
  image,
  cta,
  helperText = '',
  badge = '',
  disabled = false,
  onResolve,
  entered,
  prefersReducedMotion,
  delayMs = 0,
}) {
  const interactive = !disabled && typeof onResolve === 'function';

  return (
    <button
      type="button"
      onClick={interactive ? () => onResolve(optionId) : undefined}
      aria-disabled={disabled}
      title={disabled && helperText ? helperText : undefined}
      className={[
        'group relative flex flex-col overflow-hidden rounded-[1.5rem] border bg-card/78 p-4 text-left focus:outline-none focus:ring-2 focus:ring-primary/60 focus:ring-offset-2 focus:ring-offset-background',
        interactive
          ? 'cursor-pointer transition hover:-translate-y-1 hover:border-primary/60 hover:bg-card hover:shadow-2xl'
          : 'cursor-not-allowed border-border/60 bg-card/60 opacity-80',
      ].join(' ')}
      style={{
        width: '100%',
        maxWidth: '26rem',
        flex: '1 1 22rem',
        boxShadow: interactive
          ? '0 24px 60px -42px rgba(15, 23, 42, 0.85)'
          : '0 20px 44px -44px rgba(15, 23, 42, 0.75)',
        ...getEnterStyle(entered, prefersReducedMotion, delayMs),
      }}
    >
      {badge ? (
        <span className="absolute right-4 top-4 z-10 rounded-full border border-border/70 bg-background/85 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {badge}
        </span>
      ) : null}

      {image ? (
        <div className="mb-5 flex h-56 items-center justify-center overflow-hidden rounded-2xl border border-border/60 bg-muted/20 p-4">
          <img
            src={image}
            alt=""
            aria-hidden="true"
            className="max-h-full max-w-full object-contain"
            draggable={false}
          />
        </div>
      ) : (
        <div className="mb-4 inline-flex w-fit rounded-full border border-primary/20 bg-primary/10 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-primary">
          {badge || cta}
        </div>
      )}

      <h2 className="text-lg font-semibold text-foreground">{label}</h2>
      {description ? (
        <p className="mt-3 flex-1 text-sm leading-6 text-muted-foreground">{description}</p>
      ) : null}
      {helperText ? (
        <p className={[
          'mt-4 text-xs leading-5',
          disabled ? 'text-warning' : 'text-muted-foreground',
        ].join(' ')}>
          {helperText}
        </p>
      ) : null}

      <span
        className={[
          'mt-5 inline-flex min-h-11 items-center justify-center rounded-xl border px-4 py-3 text-sm font-semibold transition-colors',
          disabled
            ? 'border-border/70 bg-muted text-muted-foreground'
            : 'border-primary/40 bg-primary text-primary-foreground group-hover:bg-primary/90',
        ].join(' ')}
      >
        {cta}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// TransitionActionPanel — layout wrapper for completion, confirm, and info screens
// ---------------------------------------------------------------------------

const ACTION_ICON_SUCCESS = (
  <div className="mx-auto mb-5 flex h-20 w-20 items-center justify-center rounded-full border-2 border-success/30 bg-success/10">
    <svg className="h-10 w-10 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  </div>
);

const ACTION_ICON_WARNING = (
  <div className="mx-auto mb-5 flex h-20 w-20 items-center justify-center rounded-full border-2 border-warning/30 bg-warning/10">
    <svg className="h-10 w-10 text-warning" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
    </svg>
  </div>
);

const ACTION_ICON_INFO = (
  <div className="mx-auto mb-5 flex h-20 w-20 items-center justify-center rounded-full border-2 border-primary/30 bg-primary/10">
    <svg className="h-10 w-10 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  </div>
);

const ACTION_ICONS = { success: ACTION_ICON_SUCCESS, warning: ACTION_ICON_WARNING, info: ACTION_ICON_INFO };

/**
 * Full-height layout wrapper for completion, confirmation, and info transition
 * components. Provides the icon, eyebrow/title/body header, ambient glow, and
 * a centered action slot. Wrap TransitionActionButton elements as children.
 *
 * icon: 'success' | 'warning' | 'info' | null
 */
export function TransitionActionPanel({
  overlayTitleId,
  overlayDescriptionId,
  eyebrow = 'Next Step',
  title,
  body,
  icon = null,
  entered,
  prefersReducedMotion,
  children,
}) {
  return (
    <div className="relative min-h-full flex-1 overflow-hidden">
      <div aria-hidden="true" className="pointer-events-none absolute -left-16 top-0 h-44 w-44 rounded-full bg-primary/10 blur-3xl" />
      <div aria-hidden="true" className="pointer-events-none absolute right-0 top-10 h-52 w-52 rounded-full bg-secondary/10 blur-3xl" />

      <div
        className="relative p-6 text-center sm:p-8"
        style={getEnterStyle(entered, prefersReducedMotion, 0, 12)}
      >
        {icon && ACTION_ICONS[icon] ? ACTION_ICONS[icon] : null}

        <p className="text-xs font-semibold uppercase tracking-widest text-primary/80">{eyebrow}</p>
        <h1 id={overlayTitleId} className="mt-3 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
          {title}
        </h1>
        {body ? (
          <p id={overlayDescriptionId} className="mx-auto mt-4 max-w-xl text-sm leading-7 text-muted-foreground sm:text-base">
            {body}
          </p>
        ) : null}

        {children ? (
          <div className="mt-8">{children}</div>
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TransitionActionButton — styled button for use inside TransitionActionPanel
// ---------------------------------------------------------------------------

/**
 * Action button for use inside TransitionActionPanel.
 *
 * variant: 'primary' (filled) | 'secondary' (muted outline)
 *
 * For multi-button layouts, wrap in:
 *   <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-center">
 */
export function TransitionActionButton({
  label,
  variant = 'primary',
  onClick,
  disabled = false,
}) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      className={[
        'min-h-12 rounded-xl border px-6 py-3 text-sm font-semibold transition focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-background disabled:cursor-not-allowed disabled:opacity-60',
        variant === 'primary'
          ? 'border-primary/40 bg-primary text-primary-foreground hover:bg-primary/90 focus:ring-primary/50'
          : 'border-border/70 bg-muted text-muted-foreground hover:bg-muted/70 focus:ring-border',
      ].join(' ')}
    >
      {label}
    </button>
  );
}
