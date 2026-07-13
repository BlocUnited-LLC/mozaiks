import { useState } from 'react';

import { cn } from '../lib/cn.js';

function normalizePoints(data) {
  if (!Array.isArray(data)) return [];
  return data
    .filter((point) => point && typeof point === 'object')
    .map((point, index) => ({
      id: point.id || `${point.label || 'point'}-${index}`,
      label: point.label || '',
      detail: point.detail || '',
      value: Number(point.value || 0),
      extras: Array.isArray(point.extras) ? point.extras : null,
    }));
}

function BarTooltip({ point, formatPointValue }) {
  const extras = point.extras;
  return (
    <div className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 whitespace-nowrap rounded-md border border-border/60 bg-popover px-3 py-2 shadow-md">
      {extras
        ? extras.map((item) => (
            <div key={item.label} className="flex items-center justify-between gap-4 text-xs">
              <span className="text-muted-foreground">{item.label}</span>
              <span className="font-medium text-foreground">{item.value}</span>
            </div>
          ))
        : (
          <>
            <div className="mb-1 text-xs font-semibold text-foreground">{point.detail || point.label}</div>
            <div className="text-xs text-muted-foreground">{formatPointValue(point.value)}</div>
          </>
        )}
    </div>
  );
}

function defaultFormatValue(value) {
  return new Intl.NumberFormat(undefined, {
    notation: Math.abs(Number(value)) >= 1000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(Number(value || 0));
}

export function UsageTrendPanel({
  title,
  subtitle = null,
  metricLabel,
  metricValue,
  metricDetail = null,
  data = [],
  sideItems = [],
  action = null,
  emptyLabel = 'No usage recorded yet',
  formatPointValue = defaultFormatValue,
  className = '',
}) {
  const [activePointId, setActivePointId] = useState(null);
  const points = normalizePoints(data);
  const maxValue = Math.max(...points.map((point) => point.value), 0);
  const hasValues = maxValue > 0;
  const normalizedSideItems = Array.isArray(sideItems)
    ? sideItems.filter((item) => item && typeof item === 'object' && item.label)
    : [];

  return (
    <section
      className={cn(
        'overflow-hidden rounded-[1.35rem] border border-border/42 bg-background/28 shadow-sm shadow-black/5',
        className,
      )}
    >
      <div className="grid lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="min-w-0 p-4 sm:p-5 lg:p-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0">
              {title ? <h2 className="text-base font-semibold text-foreground sm:text-lg">{title}</h2> : null}
              {subtitle ? <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground/86">{subtitle}</p> : null}
            </div>
            {action ? <div className="shrink-0">{action}</div> : null}
          </div>

          <div className="mt-6">
            <div className="text-[12px] font-medium text-muted-foreground/82">{metricLabel}</div>
            <div className="mt-2 flex flex-wrap items-end gap-x-3 gap-y-1">
              <div className="text-3xl font-semibold leading-none text-foreground sm:text-4xl">{metricValue}</div>
              {metricDetail ? <div className="pb-1 text-sm text-muted-foreground/86">{metricDetail}</div> : null}
            </div>
          </div>

          <div className="mt-5 border-b border-border/38">
            {points.length > 0 ? (
              <div
                role="img"
                aria-label={`${metricLabel || title || 'Usage'} trend`}
                className="flex h-72 items-end gap-2 px-1 pt-4"
              >
                {points.map((point, index) => {
                  const height = hasValues ? Math.max(4, (point.value / maxValue) * 100) : 0;
                  const showLabel = index === 0 || index === points.length - 1 || points.length <= 8;
                  return (
                    <div key={point.id} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-2">
                      <div className="relative flex h-60 w-full items-end justify-center">
                        <div
                          className={cn(
                            'w-full max-w-12 rounded-t-md transition-colors',
                            point.value > 0 ? 'bg-primary/70 hover:bg-primary/90' : 'bg-muted-foreground/20',
                          )}
                          style={{
                            height: point.value > 0 ? `${height}%` : '0.25rem',
                            minHeight: point.value > 0 ? '0.4rem' : undefined,
                          }}
                          onMouseEnter={() => setActivePointId(point.id)}
                          onMouseLeave={() => setActivePointId(null)}
                        />
                        {activePointId === point.id && (
                          <BarTooltip point={point} formatPointValue={formatPointValue} />
                        )}
                      </div>
                      <div className="h-4 w-full truncate text-center text-[11px] text-muted-foreground/78">
                        {showLabel ? point.label : ''}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="flex h-full items-center justify-center px-4 text-sm text-muted-foreground">
                {emptyLabel}
              </div>
            )}
          </div>
        </div>

        {normalizedSideItems.length > 0 ? (
          <aside className="border-t border-border/38 bg-card/18 lg:border-l lg:border-t-0">
            {normalizedSideItems.map((item, index) => (
              <div
                key={item.id || item.label}
                className={cn(
                  'px-4 py-4 sm:px-5',
                  index < normalizedSideItems.length - 1 && 'border-b border-border/32',
                )}
              >
                <div className="text-[12px] font-medium text-muted-foreground/82">{item.label}</div>
                <div className="mt-2 break-words text-xl font-semibold text-foreground">{item.value}</div>
                {item.detail ? <div className="mt-2 text-sm leading-6 text-muted-foreground/86">{item.detail}</div> : null}
              </div>
            ))}
          </aside>
        ) : null}
      </div>
    </section>
  );
}

export default UsageTrendPanel;
