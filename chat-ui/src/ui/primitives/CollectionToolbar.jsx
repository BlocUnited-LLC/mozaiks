import { cn } from '../lib/cn.js';

function SearchIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Z" />
    </svg>
  );
}

function normalizeOptions(options) {
  return Array.isArray(options)
    ? options.filter((option) => option && typeof option === 'object' && option.label && option.value !== undefined)
    : [];
}

export function CollectionToolbar({
  searchValue = '',
  onSearchChange,
  searchPlaceholder = 'Search...',
  filters = [],
  activeFilter,
  onFilterChange,
  sortOptions = [],
  sortValue,
  onSortChange,
  sortLabel = 'Sort',
  actions = null,
  className = '',
}) {
  const normalizedFilters = normalizeOptions(filters);
  const normalizedSortOptions = normalizeOptions(sortOptions);

  return (
    <div className={cn('space-y-4 border-b border-border/32 pb-4', className)}>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <label className="relative block w-full max-w-2xl">
          <span className="sr-only">{searchPlaceholder}</span>
          <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-muted-foreground/95">
            <SearchIcon />
          </span>
          <input
            type="search"
            value={searchValue}
            onChange={(event) => onSearchChange?.(event.target.value)}
            placeholder={searchPlaceholder}
            className="h-12 w-full rounded-[var(--shell-control-radius,1rem)] border border-border/70 bg-background/72 pl-11 pr-4 text-sm text-foreground shadow-sm shadow-black/10 outline-none transition placeholder:text-muted-foreground hover:border-border hover:bg-background/84 focus:border-primary/55 focus:bg-background/90 focus:ring-2 focus:ring-primary/18"
          />
        </label>

        <div className="flex flex-wrap items-center gap-3">
          {actions}
          {normalizedSortOptions.length > 0 ? (
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <span>{sortLabel}</span>
              <select
                value={sortValue}
                onChange={(event) => onSortChange?.(event.target.value)}
                className="h-11 rounded-[var(--shell-control-radius,1rem)] border border-border/70 bg-background/72 px-3 text-sm font-medium text-foreground outline-none transition hover:border-border hover:bg-background/84 focus:border-primary/55 focus:ring-2 focus:ring-primary/18"
              >
                {normalizedSortOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
      </div>

      {normalizedFilters.length > 0 ? (
        <div className="flex flex-nowrap gap-2 overflow-x-auto pb-1 sm:flex-wrap sm:gap-5 sm:overflow-visible sm:pb-0">
          {normalizedFilters.map((filter) => {
            const selected = filter.value === activeFilter;
            return (
              <button
                key={filter.value}
                type="button"
                onClick={() => onFilterChange?.(filter.value)}
                className={cn(
                  'relative shrink-0 rounded-full border px-3 py-1.5 text-[13px] font-semibold transition sm:-mb-[17px] sm:rounded-none sm:border-x-0 sm:border-t-0 sm:border-b sm:border-transparent sm:px-0 sm:pb-3 sm:pt-0',
                  selected
                    ? 'border-primary/55 bg-primary/10 text-foreground sm:bg-transparent'
                    : 'border-border/58 bg-background/48 text-muted-foreground hover:border-border hover:text-foreground sm:border-transparent sm:bg-transparent',
                )}
              >
                {filter.label}
                {filter.count !== undefined ? (
                  <span className="ml-2 text-[12px] text-muted-foreground/80">{filter.count}</span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

export default CollectionToolbar;
