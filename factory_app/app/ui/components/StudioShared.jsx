import { Button } from '@mozaiks/chat-ui/ui';
import {
  ErrorState,
  IconButton,
  InlineEmptyState,
  LinkButton,
  LoadingState,
  Metric,
  Panel,
  SegmentedBar,
  SegmentedControl,
  SlideOver,
  StatusPill,
  SurfaceCard,
} from '@mozaiks/chat-ui/ui';

/**
 * Factory Studio UI adapter.
 *
 * Reusable visual primitives are owned by chat-ui. This file only keeps
 * Factory-local aliases and tiny adapters used by first-party Studio screens.
 */

export const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) || '';

export {
  IconButton,
  LinkButton,
  Metric,
  Panel,
  SegmentedBar,
  SegmentedControl,
  StatusPill,
  SurfaceCard,
};

export function ActionButton({
  children,
  onClick,
  disabled = false,
  variant = 'primary',
  size = 'default',
  className = '',
}) {
  const mappedVariant = variant === 'destructive' ? 'danger' : variant;
  const mappedSize = size === 'md' ? 'default' : size;

  return (
    <Button
      variant={mappedVariant}
      size={mappedSize}
      disabled={disabled}
      onClick={onClick}
      className={className}
    >
      {children}
    </Button>
  );
}

export const StudioInlineEmptyState = InlineEmptyState;
export const StudioSlideOver = SlideOver;
export const StudioLoadingState = LoadingState;
export const StudioErrorState = ErrorState;
