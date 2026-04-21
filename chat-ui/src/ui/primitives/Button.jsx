/**
 * Button primitive — wraps the base Button component.
 *
 * Schema properties (from DESIGN_SYSTEM_SPEC):
 *   label    {string}  — button text
 *   variant  {string}  — primary | secondary | danger | ghost | outline | link
 *   size     {string}  — sm | default | lg | icon
 *   icon     {string}  — react-icons name (optional)
 *   disabled {boolean}
 *   onClick  {Function}
 *
 * Agent event: none (Button emits events; it does not receive them)
 * UI action event emitted: defined by the page definition's trigger block,
 * handled by the page renderer calling the action executor.
 */

import { Button as BaseButton } from '../base/components/button.jsx';
import { cn } from '../lib/cn.js';

export function Button({
  label,
  variant = 'primary',
  size = 'default',
  icon,
  disabled = false,
  onClick,
  className,
  children,
  ...props
}) {
  return (
    <BaseButton
      variant={variant}
      size={size}
      disabled={disabled}
      onClick={onClick}
      className={cn(className)}
      {...props}
    >
      {icon && <span className="shrink-0">{icon}</span>}
      {label ?? children}
    </BaseButton>
  );
}
