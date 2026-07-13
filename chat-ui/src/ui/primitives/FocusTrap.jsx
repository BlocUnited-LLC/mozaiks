/**
 * FocusTrap — constrains keyboard Tab focus within a subtree.
 *
 * Use inside modal dialogs, slide-overs, or any overlay where focus must
 * stay contained until the overlay is dismissed.  Automatically restores
 * focus to the previously-focused element when unmounted.
 *
 * The Dialog and SlideOver primitives use FocusTrap internally, so most
 * generated app code does not need it directly.  Use it only when building
 * a custom overlay that cannot use Dialog or SlideOver.
 *
 * Props:
 *   children  {ReactNode}   — content to trap focus within
 *   active    {boolean}     — whether the trap is active (default: true)
 *   className {string}      — optional wrapper class
 *
 * Behaviour:
 *   - On mount (when active): saves the focused element, moves focus to the
 *     first focusable child, and intercepts Tab/Shift+Tab to cycle within.
 *   - On unmount: restores focus to the saved element.
 *   - When active becomes false: the trap is released but the component
 *     remains mounted (useful for animated exits).
 */
import { useEffect, useRef } from 'react';

const FOCUSABLE_SELECTORS = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  'details > summary',
].join(', ');

export function FocusTrap({ children, active = true, className }) {
  const containerRef = useRef(null);
  const savedFocusRef = useRef(null);

  // Save + restore focus
  useEffect(() => {
    if (!active) return;
    savedFocusRef.current = document.activeElement;

    const firstFocusable = containerRef.current?.querySelector(FOCUSABLE_SELECTORS);
    firstFocusable?.focus();

    return () => {
      savedFocusRef.current?.focus?.();
    };
  }, [active]);

  // Trap Tab / Shift+Tab
  useEffect(() => {
    if (!active) return;

    const handleKeyDown = (e) => {
      if (e.key !== 'Tab') return;
      const container = containerRef.current;
      if (!container) return;

      const focusable = Array.from(container.querySelectorAll(FOCUSABLE_SELECTORS)).filter(
        (el) => !el.closest('[aria-hidden="true"]'),
      );
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [active]);

  return (
    <div ref={containerRef} className={className}>
      {children}
    </div>
  );
}
