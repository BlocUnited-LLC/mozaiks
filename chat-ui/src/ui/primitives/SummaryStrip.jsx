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
        'min-w-0 w-full overflow-hidden rounded-lg border border-border/45 bg-card/[0.18] shadow-[0_1px_0_rgba(255,255,255,0.025)]',
        className,
      )}
      aria-label="Summary metrics"
    >
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,10rem),1fr))] gap-px bg-border/35">
        {normalizedItems.map((item) => (
          <div
            key={item.id || item.label}
            className="min-h-[5.75rem] min-w-0 bg-card/34 px-4 py-3.5 sm:px-5"
          >
            <div className="whitespace-normal text-[12px] font-medium text-muted-foreground/84 [overflow-wrap:anywhere]">{item.label}</div>
            <div className="mt-1.5 flex min-w-0 flex-wrap items-end gap-x-2 gap-y-1">
              <div className="min-w-0 max-w-full whitespace-normal text-xl font-semibold leading-none text-foreground [overflow-wrap:anywhere]">
                {item.value}
              </div>
              {item.detail ? (
                <div className="hidden min-w-0 max-w-full whitespace-normal pb-0.5 text-[11px] text-muted-foreground/88 [overflow-wrap:anywhere] sm:block sm:pb-1">{item.detail}</div>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default SummaryStrip;
