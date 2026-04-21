/**
 * Alert primitive — inline contextual message with optional dismiss.
 *
 * Schema properties:
 *   id       {string}  — component id for event routing
 *   message  {string}  — alert body text
 *   title    {string}  — optional bold title
 *   variant  {string}  — "info" | "success" | "warning" | "destructive" | "default"
 *   dismissible {boolean}
 *
 * Agent event: ui.alert.show
 *   payload: { component_id, message, title, variant }
 *   — replaces the current content and makes the alert visible
 */

import { useState } from 'react';
import { Alert as BaseAlert, AlertTitle, AlertDescription } from '../base/components/alert.jsx';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

export function Alert({
  id,
  message: initialMessage,
  title: initialTitle,
  variant: initialVariant = 'default',
  dismissible = false,
  className,
}) {
  const [visible,  setVisible]  = useState(!!initialMessage);
  const [message,  setMessage]  = useState(initialMessage);
  const [title,    setTitle]    = useState(initialTitle);
  const [variant,  setVariant]  = useState(initialVariant);

  useAppEvent('ui.alert.show', id, (payload) => {
    if (payload.message)  setMessage(payload.message);
    if (payload.title)    setTitle(payload.title);
    if (payload.variant)  setVariant(payload.variant);
    setVisible(true);
  });

  if (!visible || !message) return null;

  return (
    <BaseAlert variant={variant} className={cn('relative', className)}>
      {title && <AlertTitle>{title}</AlertTitle>}
      <AlertDescription>{message}</AlertDescription>
      {dismissible && (
        <button
          onClick={() => setVisible(false)}
          className="absolute right-3 top-3 text-muted-foreground hover:text-foreground text-xs"
          aria-label="Dismiss"
        >
          ✕
        </button>
      )}
    </BaseAlert>
  );
}
