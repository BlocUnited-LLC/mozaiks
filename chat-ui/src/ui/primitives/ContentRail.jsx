import { Grid } from './Grid.jsx';
import { InlineEmptyState } from './Surface.jsx';
import { cn } from '../lib/cn.js';

function normalizeItems(items) {
  return Array.isArray(items) ? items.filter(Boolean) : [];
}

export function ContentRail({
  title,
  subtitle,
  items = [],
  renderItem,
  emptyTitle,
  emptyDescription,
  columns = 3,
  gap = '4',
  className = '',
  headingClassName = '',
  titleClassName = '',
  subtitleClassName = '',
}) {
  const normalizedItems = normalizeItems(items);

  return (
    <section className={cn('space-y-4', className)}>
      {(title || subtitle) ? (
        <div className={headingClassName}>
          {title ? <h2 className={cn('text-lg font-semibold tracking-[-0.02em] text-foreground', titleClassName)}>{title}</h2> : null}
          {subtitle ? <p className={cn('mt-1 text-sm text-muted-foreground', subtitleClassName)}>{subtitle}</p> : null}
        </div>
      ) : null}
      {normalizedItems.length > 0 ? (
        <Grid columns={columns} gap={gap}>
          {normalizedItems.map((item, index) => (
            <div key={item?.id || item?.key || index}>{typeof renderItem === 'function' ? renderItem(item, index) : item}</div>
          ))}
        </Grid>
      ) : (
        <InlineEmptyState title={emptyTitle} description={emptyDescription} />
      )}
    </section>
  );
}

export default ContentRail;
