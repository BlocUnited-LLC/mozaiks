import { cn } from '../lib/cn.js';

/**
 * MetricSummaryStrip — a compact KPI row with comparison deltas.
 *
 * Extends the SummaryStrip visual language with:
 *  - a delta indicator per item (direction arrow + text, never color alone)
 *  - optional click-through per item for metric drill-down
 *  - a "pending" treatment for metrics without recorded data
 *
 * items: [{
 *   id, label, value,
 *   delta: { label, tone: 'positive'|'negative'|'neutral', srLabel? } | null,
 *   detail?: string,
 *   pending?: boolean,
 *   onSelect?: () => void,
 * }]
 */

const DELTA_TONES = {
  positive: 'text-success',
  negative: 'text-destructive',
  neutral: 'text-muted-foreground',
};

function DeltaArrow({ direction }) {
  if (direction === 'flat') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" className="h-3 w-3 shrink-0" aria-hidden="true">
        <line x1="5" y1="12" x2="19" y2="12" />
      </svg>
    );
  }
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('h-3 w-3 shrink-0', direction === 'down' && 'rotate-180')}
      aria-hidden="true"
    >
      <polyline points="6 15 12 9 18 15" />
    </svg>
  );
}

function deltaDirection(delta) {
  if (!delta || delta.tone === 'neutral') return 'flat';
  return delta.rising === false ? 'down' : delta.rising === true ? 'up' : 'flat';
}

function MetricCell({ item, isFirst, isLast }) {
  const clickable = typeof item.onSelect === 'function';
  const delta = item.delta || null;
  const Wrapper = clickable ? 'button' : 'div';

  return (
    <Wrapper
      type={clickable ? 'button' : undefined}
      onClick={clickable ? item.onSelect : undefined}
      aria-label={
        clickable
          ? `${item.label}: ${item.pending ? 'no data yet' : item.value}${delta?.srLabel ? `, ${delta.srLabel}` : ''}. View details`
          : undefined
      }
      className={cn(
        'min-h-[5.75rem] min-w-0 bg-card/34 px-4 py-3.5 text-left sm:px-5',
        isFirst && 'md:rounded-l-[inherit]',
        isLast && 'md:rounded-r-[inherit]',
        clickable &&
          'cursor-pointer transition-colors hover:bg-card/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
      )}
    >
      <div className="truncate text-[12px] font-medium text-foreground/75">{item.label}</div>
      <div className="mt-1.5 flex min-w-0 flex-wrap items-end gap-x-2 gap-y-1">
        <div
          className={cn(
            'min-w-0 break-words text-xl font-semibold leading-none tabular-nums',
            item.pending ? 'text-muted-foreground/55' : 'text-foreground',
          )}
        >
          {item.pending ? '—' : item.value}
        </div>
        {delta && !item.pending ? (
          <div
            className={cn(
              'flex items-center gap-0.5 pb-0.5 text-[11px] font-semibold tabular-nums',
              DELTA_TONES[delta.tone] || DELTA_TONES.neutral,
            )}
          >
            <DeltaArrow direction={deltaDirection(delta)} />
            <span>{delta.label}</span>
            {delta.srLabel ? <span className="sr-only">{delta.srLabel}</span> : null}
          </div>
        ) : null}
      </div>
      {item.detail || item.pending ? (
        <div className="mt-1.5 truncate text-[11px] text-foreground/60">
          {item.pending ? item.detail || 'No data yet' : item.detail}
        </div>
      ) : null}
    </Wrapper>
  );
}

export function MetricSummaryStrip({ eyebrow = null, items = [], className }) {
  const normalizedItems = (Array.isArray(items) ? items : []).filter(
    (item) => item && typeof item === 'object' && item.label,
  );
  if (normalizedItems.length === 0) return null;

  return (
    <section className={className} aria-label={eyebrow ? `${eyebrow} metrics` : 'Summary metrics'}>
      {eyebrow ? (
        <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-widest text-foreground/80">
          {eyebrow}
        </div>
      ) : null}
      <div className="overflow-hidden rounded-lg border border-border/45 bg-card/[0.18] shadow-[0_1px_0_rgba(255,255,255,0.025)]">
        <div
          className={cn(
            'grid grid-cols-2 gap-px bg-border/35',
            // Tailwind needs literal class names; map instead of interpolating.
            normalizedItems.length === 2 && 'md:grid-cols-2',
            normalizedItems.length === 3 && 'md:grid-cols-3',
            normalizedItems.length >= 4 && 'md:grid-cols-4',
          )}
        >
          {normalizedItems.map((item, index) => (
            <MetricCell
              key={item.id || item.label}
              item={item}
              isFirst={index === 0}
              isLast={index === normalizedItems.length - 1}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

export default MetricSummaryStrip;
