import { Button } from './Button.jsx';
import { PageTitle } from './PageTitle.jsx';
import { cn } from '../lib/cn.js';

function normalizeActions(actions) {
  if (!Array.isArray(actions)) return [];
  return actions.filter((action) => action && typeof action === 'object' && action.label);
}

export function PageHeader({
  title,
  subtitle = null,
  actions = [],
  onAction,
  title_font = null,
  titleFont,
  className,
}) {
  if (titleFont !== undefined) {
    // The camelCase alias was removed; failing visibly beats silently following the page-title role.
    throw new Error('PageHeader no longer accepts titleFont: pass title_font, or omit it to follow the app page-title role.');
  }
  const visibleActions = normalizeActions(actions);

  return (
    <header className={cn('min-w-0 flex flex-col gap-4 md:flex-row md:items-end md:justify-between', className)}>
      <div className="min-w-0 space-y-2">
        <PageTitle title_font={title_font}>{title}</PageTitle>
        {subtitle ? (
          <p className="max-w-2xl text-[15px] leading-7 text-muted-foreground/90">
            {subtitle}
          </p>
        ) : null}
      </div>

      {visibleActions.length > 0 ? (
        <div className="min-w-0 flex flex-wrap items-center gap-3">
          {visibleActions.map((action, index) => {
            const actionId = action.id || `page-header-action-${index + 1}`;
            return (
              <Button
                key={actionId}
                label={action.label}
                variant={action.variant ?? 'secondary'}
                size={action.size ?? 'sm'}
                disabled={action.disabled ?? false}
                className={cn(
                  'h-auto min-h-10 max-w-full whitespace-normal [overflow-wrap:anywhere] rounded-[var(--shell-control-radius,1rem)] px-4 py-2 text-sm font-semibold shadow-none',
                  action.variant === 'primary' &&
                    'border-primary/35 bg-primary text-primary-foreground hover:border-primary/45 hover:bg-primary/92',
                  action.variant === 'outline' &&
                    'border-border/70 bg-background/18 text-foreground hover:border-border/90 hover:bg-card/55',
                )}
                onClick={() => onAction?.(actionId, action)}
              />
            );
          })}
        </div>
      ) : null}
    </header>
  );
}

export default PageHeader;
