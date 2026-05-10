import * as DialogPrimitive from '@radix-ui/react-dialog';
import { useCallback } from 'react';
import { Dialog, DialogOverlay, DialogPortal } from '../base/components/dialog.jsx';

const asObject = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return value;
};

const asString = (value) => {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
};

export function TransitionOverlayFrame({
  transition,
  titleId,
  descriptionId,
  onDismiss,
  children,
}) {
  const screenProps = asObject(transition?.ui?.props);
  const dismissible = screenProps.dismissible === true && typeof onDismiss === 'function';
  const closeLabel = asString(screenProps.close_label) ?? 'Close transition';
  const hiddenTitle = asString(screenProps.title) ?? 'Transition dialog';
  const hiddenDescription = asString(screenProps.subtitle) ?? 'Choose how you want to continue.';

  const handleOpenChange = useCallback(
    (open) => {
      if (!open && dismissible) {
        onDismiss?.();
      }
    },
    [dismissible, onDismiss],
  );

  const preventDismiss = useCallback(
    (event) => {
      if (!dismissible) {
        event.preventDefault();
      }
    },
    [dismissible],
  );

  return (
    <Dialog open onOpenChange={handleOpenChange}>
      <DialogPortal>
        <DialogOverlay className="bg-background/78 backdrop-blur-md supports-[backdrop-filter]:bg-background/62 motion-reduce:backdrop-blur-0" />
        <DialogPrimitive.Content
          onEscapeKeyDown={preventDismiss}
          onInteractOutside={preventDismiss}
          onPointerDownOutside={preventDismiss}
          className={[
            'fixed left-1/2 top-1/2 z-50 w-[min(96vw,72rem)] max-h-[88vh] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-[2rem] border border-border/80 bg-background/88 shadow-2xl shadow-black/40 outline-none backdrop-blur-xl',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
            'data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95',
            'data-[state=closed]:slide-out-to-top-[52%] data-[state=open]:slide-in-from-top-[52%]',
            'motion-reduce:data-[state=open]:animate-none motion-reduce:data-[state=closed]:animate-none',
          ].join(' ')}
        >
          <div className="sr-only">
            <DialogPrimitive.Title>{hiddenTitle}</DialogPrimitive.Title>
            <DialogPrimitive.Description>{hiddenDescription}</DialogPrimitive.Description>
          </div>

          <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 h-36 bg-gradient-to-b from-primary/12 via-transparent to-transparent" />
          <div aria-hidden="true" className="pointer-events-none absolute -left-16 top-8 h-44 w-44 rounded-full bg-primary/10 blur-3xl" />
          <div aria-hidden="true" className="pointer-events-none absolute -right-12 bottom-6 h-52 w-52 rounded-full bg-secondary/10 blur-3xl" />

          {dismissible && (
            <DialogPrimitive.Close asChild>
              <button
                type="button"
                aria-label={closeLabel}
                className="absolute right-4 top-4 z-10 inline-flex h-10 w-10 items-center justify-center rounded-full border border-border/70 bg-background/85 text-muted-foreground transition hover:border-primary/50 hover:text-foreground focus:outline-none focus:ring-2 focus:ring-primary/50"
              >
                <span aria-hidden="true" className="text-lg leading-none">×</span>
              </button>
            </DialogPrimitive.Close>
          )}

          <div className="relative max-h-[88vh] overflow-auto">{children}</div>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>
  );
}

export default TransitionOverlayFrame;