/**
 * SkipLink — keyboard navigation shortcut to bypass repeated chrome.
 *
 * Render this as the very first element in your app shell. It is visually
 * hidden until focused via keyboard Tab, at which point it appears and lets
 * keyboard-only users jump directly to the main content region.
 *
 * Usage:
 *   <SkipLink targetId="main-content" />
 *   ...
 *   <main id="main-content" tabIndex={-1}>...</main>
 *
 * Props:
 *   targetId  {string}  — id of the element to jump to (default: "main-content")
 *   label     {string}  — link text (default: "Skip to main content")
 */
import { cn } from '../lib/cn.js';

export function SkipLink({ targetId = 'main-content', label = 'Skip to main content' }) {
  return (
    <a
      href={`#${targetId}`}
      className={cn(
        // Hidden off-screen until focused
        'absolute left-0 top-0 z-[9999] -translate-y-full',
        // Appear when focused
        'focus:translate-y-0',
        // Styling
        'rounded-b bg-primary px-4 py-2 text-sm font-medium text-primary-foreground',
        'transition-transform duration-150 focus:outline-none focus:ring-2 focus:ring-ring',
      )}
    >
      {label}
    </a>
  );
}
