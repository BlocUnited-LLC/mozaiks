import { isValidElement, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { Button } from './Button.jsx';
import { cn } from '../lib/cn.js';

const STATUS_TONES = {
  default: 'border-border/70 bg-muted/28 text-muted-foreground',
  muted: 'border-border/55 bg-background/35 text-muted-foreground',
  info: 'border-primary/30 bg-primary/10 text-primary',
  primary: 'border-primary/30 bg-primary/10 text-primary',
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/35 bg-warning/12 text-warning',
  destructive: 'border-destructive/35 bg-destructive/12 text-destructive',
};

const STATUS_SIZES = {
  sm: 'min-h-6 px-2 py-1 text-[11px]',
  md: 'min-h-7 px-2.5 py-1 text-xs',
};

function PrimitiveAction({ action, defaultVariant = 'secondary' }) {
  if (!action) return null;
  if (isValidElement(action)) return action;

  if (typeof action === 'string') {
    return <Button variant={defaultVariant}>{action}</Button>;
  }

  if (typeof action !== 'object') return action;

  const label = action.label || action.title || 'Continue';
  const variant = action.variant || defaultVariant;
  const size = action.size || 'default';

  if (action.to) {
    return <LinkButton to={action.to} variant={variant} size={size}>{label}</LinkButton>;
  }

  if (action.href) {
    return (
      <Button variant={variant} size={size} asChild>
        <a href={action.href}>{label}</a>
      </Button>
    );
  }

  if (typeof action.onClick === 'function') {
    return <Button variant={variant} size={size} onClick={action.onClick}>{label}</Button>;
  }

  return null;
}

export function StatusPill({
  children,
  label,
  tone = 'default',
  size = 'sm',
  dot = false,
  className = '',
}) {
  const content = label ?? children;

  return (
    <span
      className={cn(
        'inline-flex max-w-full items-center gap-1.5 rounded-md border font-semibold leading-none',
        STATUS_SIZES[size] || STATUS_SIZES.sm,
        STATUS_TONES[tone] || STATUS_TONES.default,
        className,
      )}
    >
      {dot ? <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-70" aria-hidden="true" /> : null}
      <span className="truncate">{content}</span>
    </span>
  );
}

export function SurfaceCard({
  title,
  eyebrow,
  subtitle = null,
  headerAction = null,
  children,
  accent = false,
  className = '',
}) {
  return (
    <section
      className={cn(
        'rounded-lg border p-4 shadow-sm shadow-black/5 sm:p-5',
        accent ? 'border-primary/24 bg-card/72' : 'border-border/55 bg-card/40',
        className,
      )}
    >
      {eyebrow ? (
        <div className="mb-2 text-[11px] font-medium text-muted-foreground/78">{eyebrow}</div>
      ) : null}
      {(title || subtitle || headerAction) ? (
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            {title ? <h2 className="text-lg font-semibold text-foreground">{title}</h2> : null}
            {subtitle ? <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground/88">{subtitle}</p> : null}
          </div>
          {headerAction ? <div className="shrink-0">{headerAction}</div> : null}
        </div>
      ) : null}
      {children ? <div className={title || subtitle || headerAction ? 'mt-4' : undefined}>{children}</div> : null}
    </section>
  );
}

export function Metric({ label, value, detail = null, className = '' }) {
  return (
    <div className={cn('min-h-[6.75rem] rounded-lg border border-border/55 bg-background/38 p-4', className)}>
      <div className="text-[12px] font-medium text-muted-foreground/82">{label}</div>
      <div className="mt-2 break-words text-2xl font-semibold text-foreground">{value}</div>
      {detail ? <div className="mt-2 text-sm leading-6 text-muted-foreground/86">{detail}</div> : null}
    </div>
  );
}

export function Panel({ title, eyebrow = null, subtitle = null, action = null, children, className = '' }) {
  return (
    <section className={cn('min-w-0 rounded-lg border border-border/55 bg-background/30 p-4 shadow-sm shadow-black/5', className)}>
      {(eyebrow || action) ? (
        <div className="mb-3 flex items-start justify-between gap-3">
          {eyebrow ? <div className="text-[11px] font-medium text-muted-foreground/78">{eyebrow}</div> : <span />}
          {action ? <div className="shrink-0">{action}</div> : null}
        </div>
      ) : null}
      {title ? <h3 className="text-sm font-semibold text-foreground">{title}</h3> : null}
      {subtitle ? <p className="mt-2 text-sm leading-6 text-muted-foreground/86">{subtitle}</p> : null}
      {children ? <div className="mt-4">{children}</div> : null}
    </section>
  );
}

export function SegmentedBar({ segments = [], className = '' }) {
  const toneClasses = {
    default: 'bg-muted-foreground/30',
    primary: 'bg-primary/70',
    success: 'bg-success/70',
    warning: 'bg-warning/70',
    destructive: 'bg-destructive/70',
  };

  const normalizedSegments = Array.isArray(segments)
    ? segments.filter((segment) => Number(segment?.value || 0) > 0)
    : [];
  const total = normalizedSegments.reduce((sum, segment) => sum + Number(segment.value || 0), 0);

  return (
    <div className={className}>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-muted/70">
        {total > 0 ? normalizedSegments.map((segment) => (
          <div
            key={segment.id || segment.label}
            className={toneClasses[segment.tone] || toneClasses.default}
            style={{ width: `${(Number(segment.value || 0) / total) * 100}%` }}
            title={segment.label || undefined}
          />
        )) : (
          <div className="h-full w-full bg-muted-foreground/20" />
        )}
      </div>
    </div>
  );
}

export function LinkButton({ to, children, variant = 'secondary', size = 'default', className = '' }) {
  return (
    <Button variant={variant} size={size} asChild className={className}>
      <Link to={to}>{children}</Link>
    </Button>
  );
}

export function SegmentedControl({ options = [], value, onChange, className = '' }) {
  return (
    <div className={cn('inline-flex flex-wrap gap-5 border-b border-border/35', className)}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange?.(option.value)}
            className={cn(
              'relative -mb-px border-b border-transparent pb-2 text-[13px] font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
              active ? 'border-primary/45 text-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

export function InlineEmptyState({ title, description, action = null, className = '' }) {
  return (
    <div className={cn('rounded-lg border border-border/55 bg-background/28 px-5 py-5', className)}>
      <div className="max-w-2xl">
        <div className="text-base font-semibold text-foreground">{title}</div>
        {description ? <p className="mt-2 text-sm leading-6 text-muted-foreground/86">{description}</p> : null}
        {action ? <div className="mt-4"><PrimitiveAction action={action} /></div> : null}
      </div>
    </div>
  );
}

export function IconButton({ onClick, label, disabled = false, className = '' }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      disabled={disabled}
      className={cn(
        'inline-flex h-9 w-9 items-center justify-center rounded-md border border-border/70 bg-muted/30 text-muted-foreground transition hover:border-border hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
    >
      <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 6l12 12M18 6 6 18" />
      </svg>
    </button>
  );
}

function readShellChromeInsets() {
  if (typeof document === 'undefined') {
    return { top: 0, bottom: 0 };
  }

  const shellHeader = document.querySelector('header');
  const shellFooter = document.querySelector('.shell-footer');
  const shellMobileBottomBar = document.querySelector('.shell-mobile-bottom-bar');
  const footerHeight = Math.ceil(shellFooter?.getBoundingClientRect().height || 0);
  const mobileBottomBarHeight = Math.ceil(shellMobileBottomBar?.getBoundingClientRect().height || 0);

  return {
    top: Math.max(0, Math.ceil(shellHeader?.getBoundingClientRect().height || 0)),
    bottom: Math.max(0, footerHeight, mobileBottomBarHeight),
  };
}

function useShellChromeInsets(active) {
  const [insets, setInsets] = useState(() => readShellChromeInsets());

  useEffect(() => {
    if (!active) return undefined;

    const updateInsets = () => setInsets(readShellChromeInsets());
    updateInsets();

    if (typeof window !== 'undefined') {
      window.addEventListener('resize', updateInsets);
    }

    let observer = null;
    if (typeof ResizeObserver !== 'undefined' && typeof document !== 'undefined') {
      observer = new ResizeObserver(updateInsets);
      const shellHeader = document.querySelector('header');
      const shellFooter = document.querySelector('.shell-footer');
      const shellMobileBottomBar = document.querySelector('.shell-mobile-bottom-bar');
      if (shellHeader) observer.observe(shellHeader);
      if (shellFooter) observer.observe(shellFooter);
      if (shellMobileBottomBar) observer.observe(shellMobileBottomBar);
    }

    return () => {
      if (typeof window !== 'undefined') {
        window.removeEventListener('resize', updateInsets);
      }
      observer?.disconnect();
    };
  }, [active]);

  return insets;
}

export function SlideOver({
  open,
  title,
  description = null,
  onClose,
  children,
  footer = null,
  maxWidthClass = 'max-w-xl',
  backdrop = 'blur',
}) {
  const { top, bottom } = useShellChromeInsets(open);
  const useBlurBackdrop = backdrop !== 'dim';

  useEffect(() => {
    if (!open) return undefined;

    function handleKeyDown(event) {
      if (event.key === 'Escape') onClose?.();
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    if (!open || typeof document === 'undefined') return undefined;

    const floatingRoots = [
      document.getElementById('mozaiks-embed-root'),
      ...Array.from(document.querySelectorAll('.widget-safe-bottom')),
    ].filter(Boolean);

    const previousState = floatingRoots.map((node) => ({
      node,
      visibility: node.style.visibility,
      pointerEvents: node.style.pointerEvents,
      ariaHidden: node.getAttribute('aria-hidden'),
    }));

    previousState.forEach(({ node }) => {
      node.style.visibility = 'hidden';
      node.style.pointerEvents = 'none';
      node.setAttribute('aria-hidden', 'true');
    });

    return () => {
      previousState.forEach(({ node, visibility, pointerEvents, ariaHidden }) => {
        node.style.visibility = visibility;
        node.style.pointerEvents = pointerEvents;
        if (ariaHidden == null) {
          node.removeAttribute('aria-hidden');
        } else {
          node.setAttribute('aria-hidden', ariaHidden);
        }
      });
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-x-0 z-[100000]"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      style={{
        top: `calc(${top}px + 0.75rem)`,
        bottom: `calc(${bottom}px + 0.75rem)`,
      }}
    >
      <div className="relative flex h-full items-end justify-center px-0 md:justify-end md:px-6">
        <button
          type="button"
          className="absolute inset-0"
          aria-label="Close panel"
          onClick={onClose}
          style={{
            backgroundColor: useBlurBackdrop ? 'rgba(2, 6, 23, 0.62)' : 'rgba(2, 6, 23, 0.5)',
            WebkitBackdropFilter: useBlurBackdrop ? 'blur(12px)' : undefined,
            backdropFilter: useBlurBackdrop ? 'blur(12px)' : undefined,
          }}
        />
        <section
          className={cn(
            'relative flex max-h-full w-full flex-col overflow-hidden rounded-t-xl border border-border border-b-0 bg-card shadow-xl md:h-full md:rounded-xl md:border-b',
            maxWidthClass,
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-border px-4 pb-4 pt-4 sm:px-6 sm:pt-6">
            <div>
              <h2 className="text-xl font-semibold text-foreground">{title}</h2>
              {description ? <p className="mt-2 text-sm leading-7 text-muted-foreground">{description}</p> : null}
            </div>
            <IconButton onClick={onClose} label="Close overlay" />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-4 sm:px-6 sm:pb-8 sm:pt-6">{children}</div>
          {footer ? (
            <div className="border-t border-border bg-background/80 p-4 backdrop-blur-sm sm:p-6">
              {footer}
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}

export function LoadingState({ label, message, className = '' }) {
  const displayLabel = label ?? message ?? 'Loading...';

  return (
    <div
      className={cn('flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10', className)}
      role="status"
      aria-live="polite"
    >
      <div className="flex max-w-full items-center gap-3 rounded-lg border border-border/70 bg-card px-4 py-3 text-sm text-muted-foreground shadow-sm">
        <div className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-hidden="true" />
        <span className="min-w-0 break-words">{displayLabel}</span>
      </div>
    </div>
  );
}

export function ErrorState({ title = 'Unavailable', message, action = null, className = '' }) {
  return (
    <div
      className={cn('flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10', className)}
      role="alert"
      aria-live="assertive"
    >
      <div className="max-w-xl rounded-lg border border-destructive/35 bg-destructive/10 p-5 shadow-sm">
        <div className="text-xs font-semibold text-destructive">{title}</div>
        {message ? <p className="mt-3 text-sm leading-6 text-foreground">{message}</p> : null}
        {action ? <div className="mt-5"><PrimitiveAction action={action} /></div> : null}
      </div>
    </div>
  );
}
