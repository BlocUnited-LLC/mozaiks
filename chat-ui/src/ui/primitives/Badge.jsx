/**
 * Badge primitive — status label / tag.
 *
 * Schema properties:
 *   label   {string}  — display text
 *   variant {string}  — "default" | "secondary" | "destructive" | "success" | "warning" | "outline"
 */

import { Badge as BaseBadge } from '../base/components/badge.jsx';
import { cn } from '../lib/cn.js';

export function Badge({ label, variant = 'default', className, children }) {
  return (
    <BaseBadge variant={variant} className={cn(className)}>
      {label ?? children}
    </BaseBadge>
  );
}
