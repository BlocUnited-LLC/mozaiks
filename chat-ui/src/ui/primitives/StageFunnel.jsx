import { cn } from '../lib/cn.js';

/**
 * StageFunnel — an app-configurable lifecycle funnel.
 *
 * Renders schema-driven stages (declared in the app's metrics config) as
 * scaled horizontal bars with stage-to-stage conversion. Counts and rates
 * are always rendered as text; bars reinforce, they are not the only signal.
 * Stages with an onSelect drill into that stage's underlying event.
 *
 * steps: [{
 *   id, label, count, formattedCount,
 *   conversionLabel?: string,   // e.g. "62% from previous"
 *   onSelect?,
 * }]
 */

function StageRow({ step, maxCount }) {
  const clickable = typeof step.onSelect === 'function';
  const Wrapper = clickable ? 'button' : 'div';
  const count = Number(step.count || 0);
  const width = maxCount > 0 ? Math.max(2, (count / maxCount) * 100) : 0;

  return (
    <Wrapper
      type={clickable ? 'button' : undefined}
      onClick={clickable ? step.onSelect : undefined}
      aria-label={
        clickable
          ? `${step.label}: ${step.formattedCount}${step.conversionLabel ? `, ${step.conversionLabel}` : ''}. View details`
          : undefined
      }
      className={cn(
        'block w-full px-4 py-3 text-left sm:px-5',
        clickable &&
          'cursor-pointer transition-colors hover:bg-card/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset',
      )}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-sm font-medium text-foreground">{step.label}</span>
        <span className="flex shrink-0 items-baseline gap-2">
          <span className="text-sm font-semibold tabular-nums text-foreground">{step.formattedCount}</span>
          {step.conversionLabel ? (
            <span className="text-[11px] tabular-nums text-muted-foreground/78">{step.conversionLabel}</span>
          ) : null}
        </span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted/60" aria-hidden="true">
        <span
          className="block h-full rounded-full bg-primary/65"
          style={{ width: `${width}%`, minWidth: count > 0 ? '0.35rem' : 0 }}
        />
      </div>
    </Wrapper>
  );
}

export function StageFunnel({ steps = [], className }) {
  const normalizedSteps = (Array.isArray(steps) ? steps : []).filter(
    (step) => step && typeof step === 'object' && step.label,
  );
  if (normalizedSteps.length === 0) return null;
  const maxCount = Math.max(...normalizedSteps.map((step) => Number(step.count || 0)), 0);

  return (
    <div
      className={cn(
        'overflow-hidden rounded-lg border border-border/45 bg-card/[0.18] shadow-[0_1px_0_rgba(255,255,255,0.025)]',
        className,
      )}
      role="list"
      aria-label="Lifecycle funnel"
    >
      {normalizedSteps.map((step, index) => (
        <div key={step.id || step.label} role="listitem" className={cn(index > 0 && 'border-t border-border/32')}>
          <StageRow step={step} maxCount={maxCount} />
        </div>
      ))}
    </div>
  );
}

export default StageFunnel;
