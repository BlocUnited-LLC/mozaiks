import { clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Merge Tailwind classes safely, resolving conflicts in favour of the last value.
 * Used by all base components and primitives.
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
