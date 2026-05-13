import { useEffect, useState } from 'react';

const ENTRY_EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';

export function useTransitionChoiceMotion() {
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
    <div className="relative overflow-hidden">
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
