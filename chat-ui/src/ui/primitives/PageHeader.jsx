import { Button } from './Button.jsx';
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
  title_font = 'body',
  titleFont = null,
  className,
}) {
  const visibleActions = normalizeActions(actions);
  const resolvedTitleFont = titleFont || title_font;
  const titleFontClass = resolvedTitleFont === 'heading'
    ? '[font-family:var(--font-heading)] tracking-[-0.04em]'
    : '[font-family:var(--font-body)] tracking-[-0.03em]';

  return (
    <header className={cn('flex flex-col gap-4 md:flex-row md:items-end md:justify-between', className)}>
      <div className="min-w-0 space-y-2">
        <h1 className={cn('text-[2rem] font-semibold leading-none text-foreground md:text-[2.18rem]', titleFontClass)}>
          {title}
        </h1>
        {subtitle ? (
          <p className="max-w-2xl text-[15px] leading-7 text-muted-foreground/90">
            {subtitle}
          </p>
        ) : null}
      </div>

      {visibleActions.length > 0 ? (
        <div className="flex flex-wrap items-center gap-3">
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
                  'h-10 rounded-[var(--shell-control-radius,1rem)] px-4 text-sm font-semibold shadow-none',
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
