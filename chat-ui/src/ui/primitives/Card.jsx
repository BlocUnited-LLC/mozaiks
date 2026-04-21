/**
 * Card primitive — bordered container with optional title, subtitle, actions.
 *
 * Schema properties:
 *   title    {string}
 *   subtitle {string}
 *   children {ReactNode}   — nested primitives (rendered by PageRenderer)
 *   actions  {Action[]}    — rendered as buttons in the card footer
 *   className {string}
 */

import {
  Card as BaseCard,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from '../base/components/card.jsx';
import { Button } from './Button.jsx';
import { cn } from '../lib/cn.js';

export function Card({ title, subtitle, children, actions = [], className }) {
  const hasHeader = title || subtitle;
  const hasFooter = actions.length > 0;

  return (
    <BaseCard className={cn(className)}>
      {hasHeader && (
        <CardHeader>
          {title    && <CardTitle>{title}</CardTitle>}
          {subtitle && <CardDescription>{subtitle}</CardDescription>}
        </CardHeader>
      )}
      <CardContent className={cn(!hasHeader && 'pt-6')}>
        {children}
      </CardContent>
      {hasFooter && (
        <CardFooter className="gap-2">
          {actions.map((action) => (
            <Button
              key={action.id}
              label={action.label}
              variant={action.variant ?? 'secondary'}
              onClick={action.onClick}
            />
          ))}
        </CardFooter>
      )}
    </BaseCard>
  );
}
