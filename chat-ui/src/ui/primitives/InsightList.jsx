import { cn } from '../lib/cn.js';

/**
 * InsightList — a compact "needs attention" section.
 *
 * Renders deterministic, pre-computed insights as scannable rows. Severity is
 * communicated with a text label plus tone (never color alone). Rows with an
 * onSelect drill into the supporting app/metric.
 *
 * items: [{
 *   id, severity: 'attention'|'highlight'|'watch',
 *   headline, detail?, onSelect?,
 * }]
 */

const SEVERITY_META = {
  attention: { label: 'Attention', className: 'border-warning/35 bg-warning/12 text-warning' },
  highlight: { label: 'Highlight', className: 'border-success/30 bg-success/10 text-success' },
  watch: { label: 'Watch', className: 'border-border/70 bg-muted/28 text-muted-foreground' },
};

function Chevron() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4 shrink-0 text-muted-foreground/60" aria-hidden="true">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

function InsightRow({ item }) {
  const severity = SEVERITY_META[item.severity] || SEVERITY_META.watch;
  const clickable = typeof item.onSelect === 'function';
  const Wrapper = clickable ? 'button' : 'div';

  return (
    <Wrapper
      type={clickable ? 'button' : undefined}
      onClick={clickable ? item.onSelect : undefined}
      className={cn(
        'flex w-full items-center gap-3 px-4 py-3 text-left sm:px-5',
        clickable &&
          'cursor-pointer transition-colors hover:bg-card/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
      )}
    >
      <span
        className={cn(
          'inline-flex shrink-0 items-center rounded-md border px-2 py-1 text-[10px] font-semibold uppercase tracking-wider leading-none',
          severity.className,
        )}
      >
        {severity.label}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">{item.headline}</span>
        {item.detail ? (
          <span className="mt-0.5 block truncate text-xs text-muted-foreground/78">{item.detail}</span>
        ) : null}
      </span>
      {clickable ? <Chevron /> : null}
    </Wrapper>
  );
}

export function InsightList({ items = [], emptyLabel = null, className }) {
  const normalizedItems = (Array.isArray(items) ? items : []).filter(
    (item) => item && typeof item === 'object' && item.headline,
  );

  if (normalizedItems.length === 0) {
    if (!emptyLabel) return null;
    return (
      <div className={cn('rounded-lg border border-border/45 bg-card/[0.18] px-4 py-4 text-sm text-muted-foreground/80 sm:px-5', className)}>
        {emptyLabel}
      </div>
    );
  }

  return (
    <div
      className={cn(
        'overflow-hidden rounded-lg border border-border/45 bg-card/[0.18] shadow-[0_1px_0_rgba(255,255,255,0.025)]',
        className,
      )}
      role="list"
      aria-label="Portfolio insights"
    >
      {normalizedItems.map((item, index) => (
        <div key={item.id || item.headline} role="listitem" className={cn(index > 0 && 'border-t border-border/32')}>
          <InsightRow item={item} />
        </div>
      ))}
    </div>
  );
}

export default InsightList;
