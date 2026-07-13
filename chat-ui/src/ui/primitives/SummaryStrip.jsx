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
        'overflow-hidden rounded-lg border border-border/45 bg-card/[0.18] shadow-[0_1px_0_rgba(255,255,255,0.025)]',
        className,
      )}
      aria-label="Summary metrics"
    >
      <div className="grid grid-cols-2 gap-px bg-border/35 md:grid-cols-4">
        {normalizedItems.map((item, index) => (
          <div
            key={item.id || item.label}
            className={cn(
              'min-h-[5.75rem] min-w-0 bg-card/34 px-4 py-3.5 sm:px-5',
              index === 0 && 'md:rounded-l-[inherit]',
              index === normalizedItems.length - 1 && 'md:rounded-r-[inherit]',
            )}
          >
            <div className="truncate text-[12px] font-medium text-muted-foreground/84">{item.label}</div>
            <div className="mt-1.5 flex min-w-0 flex-wrap items-end gap-x-2 gap-y-1">
              <div className="min-w-0 break-words text-xl font-semibold leading-none text-foreground">
                {item.value}
              </div>
              {item.detail ? (
                <div className="hidden pb-0.5 text-[11px] text-muted-foreground/88 sm:block sm:pb-1">{item.detail}</div>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default SummaryStrip;
