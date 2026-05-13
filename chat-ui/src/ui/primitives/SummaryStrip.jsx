import { cn } from '../lib/cn.js';

function normalizeItems(items) {
  if (!Array.isArray(items)) return [];
  return items.filter((item) => item && typeof item === 'object' && item.label);
}

export function SummaryStrip({ items = [], className }) {
  const normalizedItems = normalizeItems(items);
  if (normalizedItems.length === 0) return null;

  return (
    <div
      className={cn(
        'overflow-hidden rounded-[calc(var(--core-primitive-radius,1rem)+0.35rem)] border border-border/32 bg-card/[0.16] shadow-[0_1px_0_rgba(255,255,255,0.025)]',
        className,
      )}
    >
      <div className="grid grid-cols-2 gap-px bg-border/26 md:grid-cols-4">
        {normalizedItems.map((item, index) => (
          <div
            key={item.id || item.label}
            className={cn(
              'min-w-0 bg-card/30 px-4 py-3.5 sm:px-5 sm:py-4',
              index === 0 && 'md:rounded-l-[inherit]',
              index === normalizedItems.length - 1 && 'md:rounded-r-[inherit]',
            )}
          >
            <div className="text-[12px] font-medium text-muted-foreground/84">{item.label}</div>
            <div className="mt-1 flex flex-wrap items-end gap-x-2 gap-y-1">
              <div className="text-[1.45rem] font-semibold leading-none tracking-[-0.035em] text-foreground sm:text-[1.62rem]">
                {item.value}
              </div>
              {item.detail ? (
                <div className="pb-0.5 text-[11px] text-muted-foreground/88 sm:pb-1">{item.detail}</div>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default SummaryStrip;
