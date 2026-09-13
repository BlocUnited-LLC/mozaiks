import { cn } from '../lib/cn.js';

/**
 * MetricBridge — a start → drivers → end movement explanation.
 *
 * Explains WHY a stock metric changed over a period (the classic MRR bridge):
 * a starting level, additive and subtractive drivers rendered as scaled
 * horizontal bars, and the ending level. Driver rows with an onSelect drill
 * into that driver's detail. Values are always rendered as text alongside
 * the bars, so the bars are reinforcement, not the only signal.
 *
 * props: {
 *   start: { label, formatted },
 *   end: { label, formatted },
 *   steps: [{ id, label, formatted, value, direction: 'add'|'subtract', onSelect? }],
 *   unexplained?: { formatted } | null,
 * }
 */

function DriverRow({ step, maxValue }) {
  const clickable = typeof step.onSelect === 'function';
  const Wrapper = clickable ? 'button' : 'div';
  const magnitude = Math.abs(Number(step.value || 0));
  const width = maxValue > 0 ? Math.max(2, (magnitude / maxValue) * 100) : 0;
  const isAdd = step.direction !== 'subtract';

  return (
    <Wrapper
      type={clickable ? 'button' : undefined}
      onClick={clickable ? step.onSelect : undefined}
      aria-label={clickable ? `${step.label}: ${step.formatted}. View details` : undefined}
      className={cn(
        'grid w-full grid-cols-[minmax(7rem,10rem)_minmax(0,1fr)_minmax(5rem,auto)] items-center gap-3 px-4 py-2.5 text-left sm:px-5',
        clickable &&
          'cursor-pointer transition-colors hover:bg-card/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
      )}
    >
      <span className="truncate text-sm text-muted-foreground">{step.label}</span>
      <span className="flex h-2 items-center" aria-hidden="true">
        <span
          className={cn('block h-2 rounded-full', isAdd ? 'bg-success/65' : 'bg-destructive/60')}
          style={{ width: `${width}%`, minWidth: magnitude > 0 ? '0.35rem' : 0 }}
        />
      </span>
      <span
        className={cn(
          'text-right text-sm font-medium tabular-nums',
          isAdd ? 'text-success' : 'text-destructive',
        )}
      >
        {isAdd ? '+' : '−'}{step.formatted}
      </span>
    </Wrapper>
  );
}

function LevelRow({ label, formatted, emphasized = false }) {
  return (
    <div className="grid grid-cols-[minmax(7rem,10rem)_minmax(0,1fr)_minmax(5rem,auto)] items-center gap-3 px-4 py-2.5 sm:px-5">
      <span className={cn('truncate text-sm', emphasized ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
        {label}
      </span>
      <span aria-hidden="true" />
      <span className={cn('text-right text-sm tabular-nums', emphasized ? 'font-semibold text-foreground' : 'font-medium text-foreground')}>
        {formatted}
      </span>
    </div>
  );
}

export function MetricBridge({ start, end, steps = [], unexplained = null, className }) {
  const normalizedSteps = (Array.isArray(steps) ? steps : []).filter(
    (step) => step && typeof step === 'object' && step.label,
  );
  if (!start || !end) return null;
  const maxValue = Math.max(...normalizedSteps.map((step) => Math.abs(Number(step.value || 0))), 0);

  return (
    <div
      className={cn(
        'overflow-hidden rounded-lg border border-border/45 bg-card/[0.18] shadow-[0_1px_0_rgba(255,255,255,0.025)]',
        className,
      )}
    >
      <LevelRow label={start.label} formatted={start.formatted} />
      <div className="border-t border-border/32">
        {normalizedSteps.map((step) => (
          <DriverRow key={step.id || step.label} step={step} maxValue={maxValue} />
        ))}
        {unexplained ? (
          <div className="grid grid-cols-[minmax(7rem,10rem)_minmax(0,1fr)_minmax(5rem,auto)] items-center gap-3 px-4 py-2.5 sm:px-5">
            <span className="truncate text-sm text-muted-foreground">Unattributed</span>
            <span aria-hidden="true" />
            <span className="text-right text-sm tabular-nums text-muted-foreground">
              {unexplained.formatted}
            </span>
          </div>
        ) : null}
      </div>
      <div className="border-t border-border/45">
        <LevelRow label={end.label} formatted={end.formatted} emphasized />
      </div>
    </div>
  );
}

export default MetricBridge;
