/**
 * Modal primitive — dialog overlay with optional title, description, footer actions.
 *
 * Schema properties:
 *   id          {string}   — modal id for event routing
 *   title       {string}
 *   description {string}
 *   size        {string}   — "small" | "medium" | "large" | "full"
 *   children    {ReactNode} — body content (nested primitives)
 *   actions     {Action[]} — footer buttons
 *   open        {boolean}  — controlled open state (optional)
 *   onClose     {Function}
 *
 * Agent events:
 *   ui.modal.open  { modal_id }   — opens this modal
 *   ui.modal.close { modal_id }   — closes this modal
 */

import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '../base/components/dialog.jsx';
import { Button } from './Button.jsx';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

export function Modal({
  id,
  title,
  description,
  size = 'medium',
  children,
  actions = [],
  open: controlledOpen,
  onClose,
  className,
}) {
  const [open, setOpen] = useState(controlledOpen ?? false);

  // Sync with controlled prop
  useEffect(() => {
    if (controlledOpen !== undefined) setOpen(controlledOpen);
  }, [controlledOpen]);

  useAppEvent('ui.modal.open', id, () => setOpen(true));
  useAppEvent('ui.modal.close', id, () => {
    setOpen(false);
    onClose?.();
  });

  const handleOpenChange = (next) => {
    setOpen(next);
    if (!next) onClose?.();
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent size={size} className={cn('gap-0 p-0', className)}>
        {(title || description) && (
          <DialogHeader className="px-4 pb-3 pt-4 sm:px-6 sm:pb-4 sm:pt-6">
            {title       && <DialogTitle>{title}</DialogTitle>}
            {description && <DialogDescription>{description}</DialogDescription>}
          </DialogHeader>
        )}
        <div className="px-4 pb-4 sm:px-6 sm:pb-6">{children}</div>
        {actions.length > 0 && (
          <DialogFooter className="gap-2 border-t border-border/60 bg-background/80 px-4 py-4 backdrop-blur-sm sm:px-6">
            {actions.map((action) => (
              <Button
                key={action.id}
                label={action.label}
                variant={action.variant ?? 'secondary'}
                className="w-full sm:w-auto"
                onClick={() => {
                  action.onClick?.();
                  if (action.closes_modal !== false) handleOpenChange(false);
                }}
              />
            ))}
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
