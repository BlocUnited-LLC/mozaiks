import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { Button } from './Button.jsx';
import { cn } from '../lib/cn.js';

export function StatusPill({ children, label, tone = 'default', className = '' }) {
  const tones = {
    default: 'border-border/50 bg-muted/22 text-muted-foreground',
    primary: 'border-primary/28 bg-primary/8 text-primary',
    success: 'border-success/28 bg-success/8 text-success',
    warning: 'border-warning/28 bg-warning/8 text-warning',
    destructive: 'border-destructive/28 bg-destructive/8 text-destructive',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium leading-none',
        tones[tone] || tones.default,
        className,
      )}
    >
      {label ?? children}
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
        'rounded-[calc(var(--core-primitive-radius,1rem)+0.45rem)] border p-5 shadow-sm shadow-black/5 sm:p-6',
        accent ? 'border-primary/18 bg-card/62' : 'border-border/45 bg-card/34',
        className,
      )}
    >
      {eyebrow ? (
        <div className="mb-2 text-[11px] font-medium text-muted-foreground/78">{eyebrow}</div>
      ) : null}
      {(title || subtitle || headerAction) ? (
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            {title ? <h2 className="text-lg font-semibold tracking-[-0.015em] text-foreground">{title}</h2> : null}
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
    <div className={cn('rounded-2xl border border-border/42 bg-background/34 p-4', className)}>
      <div className="text-[12px] font-medium text-muted-foreground/82">{label}</div>
      <div className="mt-2 text-2xl font-semibold tracking-[-0.025em] text-foreground">{value}</div>
      {detail ? <div className="mt-2 text-sm leading-6 text-muted-foreground/86">{detail}</div> : null}
    </div>
  );
}

export function Panel({ title, eyebrow = null, subtitle = null, action = null, children, className = '' }) {
  return (
    <section className={cn('rounded-[1.35rem] border border-border/42 bg-background/28 p-4 shadow-sm shadow-black/5', className)}>
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
              'relative -mb-px border-b border-transparent pb-2 text-[13px] font-semibold transition',
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
    <div className={cn('rounded-[1.35rem] border border-border/42 bg-background/24 px-6 py-6', className)}>
      <div className="max-w-2xl">
        <div className="text-base font-semibold text-foreground">{title}</div>
        {description ? <p className="mt-2 text-sm leading-6 text-muted-foreground/86">{description}</p> : null}
        {action ? <div className="mt-4">{action}</div> : null}
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
        'inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border/70 bg-muted/30 text-muted-foreground transition hover:border-border hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50',
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

  return {
    top: Math.max(0, Math.ceil(shellHeader?.getBoundingClientRect().height || 0)),
    bottom: Math.max(0, Math.ceil(shellFooter?.getBoundingClientRect().height || 0)),
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
      if (shellHeader) observer.observe(shellHeader);
      if (shellFooter) observer.observe(shellFooter);
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
}) {
  const { top, bottom } = useShellChromeInsets(open);

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
            backgroundColor: 'rgba(2, 6, 23, 0.62)',
            WebkitBackdropFilter: 'blur(12px)',
            backdropFilter: 'blur(12px)',
          }}
        />
        <section
          className={cn(
            'relative flex max-h-full w-full flex-col overflow-hidden rounded-t-[2rem] border border-border border-b-0 bg-card shadow-2xl md:h-full md:rounded-3xl md:border-b',
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

export function LoadingState({ label = 'Loading...', className = '' }) {
  return (
    <div className={cn('flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10', className)}>
      <div className="flex items-center gap-3 rounded-2xl border border-border bg-card px-5 py-4 text-sm text-muted-foreground shadow-sm">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        {label}
      </div>
    </div>
  );
}

export function ErrorState({ title = 'Unavailable', message, className = '' }) {
  return (
    <div className={cn('flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10', className)}>
      <div className="max-w-xl rounded-3xl border border-destructive/30 bg-destructive/10 p-6 shadow-sm">
        <div className="text-xs font-semibold text-destructive">{title}</div>
        {message ? <p className="mt-3 text-sm leading-6 text-foreground">{message}</p> : null}
      </div>
    </div>
  );
}
