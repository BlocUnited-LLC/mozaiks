/**
 * Skeleton primitive — loading placeholder shimmer.
 * Empty    primitive — empty-state display with optional message and action.
 *
 * Skeleton schema:
 *   rows    {number}  — number of skeleton rows to show (default 3)
 *   height  {string}  — Tailwind h-* class for each row (default "h-4")
 *
 * Empty schema:
 *   title   {string}
 *   message {string}
 *   action  {Action}  — optional CTA button
 *   icon    {ReactNode}
 */

import { Skeleton as BaseSkeleton } from '../base/components/skeleton.jsx';
import { Button } from './Button.jsx';
import { cn } from '../lib/cn.js';

export function Skeleton({ rows = 3, height = 'h-4', className }) {
  return (
    <div className={cn('space-y-3', className)}>
      {Array.from({ length: rows }, (_, i) => (
        <BaseSkeleton key={i} className={cn(height, i === 0 ? 'w-3/4' : i === rows - 1 ? 'w-1/2' : 'w-full')} />
      ))}
    </div>
  );
}

export function Empty({ title = 'Nothing here yet', message, action, icon, className }) {
  return (
    <div className={cn('flex flex-col items-center justify-center py-12 text-center', className)}>
      {icon && <div className="mb-4 text-4xl text-muted-foreground">{icon}</div>}
      <h3 className="text-base font-semibold text-foreground mb-1">{title}</h3>
      {message && <p className="text-sm text-muted-foreground mb-4 max-w-xs">{message}</p>}
      {action && (
        <Button
          label={action.label}
          variant={action.variant ?? 'primary'}
          onClick={action.onClick}
        />
      )}
    </div>
  );
}
