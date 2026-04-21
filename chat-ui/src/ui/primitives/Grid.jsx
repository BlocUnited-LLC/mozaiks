/**
 * Grid primitive — responsive CSS grid layout container.
 *
 * Schema properties:
 *   columns  {number}    — grid column count (default 3)
 *   gap      {string}    — Tailwind gap value: "2" | "4" | "6" | "8" (default "4")
 *   children {ReactNode} — nested primitives (rendered by PageRenderer)
 */

import { cn } from '../lib/cn.js';

const gapMap = {
  sm: 'gap-2', 'md': 'gap-4', 'lg': 'gap-6',
  '1': 'gap-1', '2': 'gap-2', '3': 'gap-3', '4': 'gap-4',
  '6': 'gap-6', '8': 'gap-8', '10': 'gap-10', '12': 'gap-12',
};

const colMap = {
  1: 'grid-cols-1',
  2: 'grid-cols-1 sm:grid-cols-2',
  3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
  4: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4',
  5: 'grid-cols-1 sm:grid-cols-3 lg:grid-cols-5',
  6: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-6',
};

export function Grid({ columns = 3, gap = '4', children, className }) {
  const normalizedColumns = Number(columns) || 3;

  return (
    <div className={cn('grid', colMap[normalizedColumns] ?? 'grid-cols-3', gapMap[String(gap)] ?? 'gap-4', className)}>
      {children}
    </div>
  );
}
