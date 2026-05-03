/**
 * AlertBanner primitive — prominent info/warning/error/success banner with optional actions.
 *
 * Schema properties:
 *   id          {string}   — component id for event routing
 *   message     {string}   — banner body text
 *   title       {string}   — optional bold heading
 *   variant     {string}   — "info" | "success" | "warning" | "destructive"
 *   dismissible {boolean}  — show dismiss button (default false)
 *   actions     {Action[]} — optional inline action buttons
 *
 * Agent event: ui.alertbanner.show
 *   payload: { component_id, message, title, variant }
 */

import { useState } from 'react';
import { Button } from './Button.jsx';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

const VARIANT_STYLES = {
  info:        { wrap: 'border-primary/40 bg-primary/10',         icon: 'ℹ', iconCls: 'text-primary',      title: 'text-primary',      body: 'text-primary/80' },
  success:     { wrap: 'border-success/40 bg-success/10',         icon: '✓', iconCls: 'text-success',      title: 'text-success',      body: 'text-success/80' },
  warning:     { wrap: 'border-warning/40 bg-warning/10',         icon: '⚠', iconCls: 'text-warning',      title: 'text-warning',      body: 'text-warning/80' },
  destructive: { wrap: 'border-destructive/40 bg-destructive/10', icon: '✕', iconCls: 'text-destructive',  title: 'text-destructive',  body: 'text-destructive/80' },
};

export function AlertBanner({
  id,
  message: initialMessage,
  title: initialTitle,
  variant: initialVariant = 'info',
  dismissible = false,
  actions = [],
  onAction,
  className,
}) {
  const [visible,  setVisible]  = useState(!!initialMessage);
  const [message,  setMessage]  = useState(initialMessage);
  const [title,    setTitle]    = useState(initialTitle);
  const [variant,  setVariant]  = useState(initialVariant);

  useAppEvent('ui.alertbanner.show', id, (payload) => {
    if (payload.message !== undefined) setMessage(payload.message);
    if (payload.title   !== undefined) setTitle(payload.title);
    if (payload.variant !== undefined) setVariant(payload.variant);
    setVisible(true);
  });

  if (!visible || !message) return null;

  const s = VARIANT_STYLES[variant] ?? VARIANT_STYLES.info;

  return (
    <div className={cn('rounded-lg border p-4', s.wrap, className)}>
      <div className="flex items-start gap-3">
        <span className={cn('text-base flex-shrink-0 mt-0.5', s.iconCls)}>{s.icon}</span>
        <div className="flex-1 min-w-0 space-y-1">
          {title && <p className={cn('text-sm font-semibold', s.title)}>{title}</p>}
          <p className={cn('text-sm leading-relaxed', s.body)}>{message}</p>
          {actions.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-1">
              {actions.map((action, i) => (
                <Button
                  key={action.id ?? i}
                  label={action.label}
                  variant={action.variant ?? (i === 0 ? 'primary' : 'outline')}
                  size="sm"
                  onClick={() => onAction?.(action.id ?? action.label, [])}
                />
              ))}
            </div>
          )}
        </div>
        {dismissible && (
          <button
            onClick={() => setVisible(false)}
            className={cn('flex-shrink-0 text-sm leading-none hover:opacity-70 transition-opacity', s.iconCls)}
            aria-label="Dismiss"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
