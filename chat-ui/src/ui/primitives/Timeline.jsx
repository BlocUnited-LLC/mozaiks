/**
 * Timeline primitive — ordered event/step list with status indicators.
 *
 * Schema properties:
 *   id      {string}   — component id for event routing
 *   title   {string}   — optional section heading
 *   items   {Item[]}   — ordered list of events/steps
 *
 * Item schema:
 *   label        {string}
 *   description  {string}
 *   status       {string}  — "done" | "active" | "pending" | "error"
 *   timestamp    {string}
 *
 * Agent event: ui.timeline.update
 *   payload: { component_id, items }  — replaces the items list
 */

import { useState, useEffect } from 'react';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

const STATUS_DOT = {
  done:    'bg-success border-success text-white',
  active:  'bg-primary border-primary text-white animate-pulse',
  pending: 'bg-muted border-border text-muted-foreground',
  error:   'bg-destructive border-destructive text-white',
};
const STATUS_ICON = { done: '✓', active: '●', pending: '○', error: '✕' };

export function Timeline({ id, title, items: initialItems = [], className }) {
  const [items, setItems] = useState(initialItems);

  useEffect(() => {
    setItems(Array.isArray(initialItems) ? initialItems : []);
  }, [initialItems]);

  useAppEvent('ui.timeline.update', id, (payload) => {
    if (Array.isArray(payload.items)) setItems(payload.items);
  });

  return (
    <div className={cn('space-y-1', className)}>
      {title && (
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-3">{title}</p>
      )}
      {items.map((item, i) => {
        const dot = STATUS_DOT[item.status] ?? STATUS_DOT.pending;
        const isLast = i === items.length - 1;
        return (
          <div key={i} className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className={cn('w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 text-[10px] font-bold', dot)}>
                {STATUS_ICON[item.status] ?? '○'}
              </div>
              {!isLast && <div className="w-px flex-1 bg-border min-h-[16px]" />}
            </div>
            <div className={cn('flex-1 min-w-0', isLast ? 'pb-0' : 'pb-4')}>
              <div className="flex items-baseline justify-between gap-2 flex-wrap">
                <p className="text-sm font-medium text-foreground">{item.label}</p>
                {item.timestamp && (
                  <span className="text-xs text-muted-foreground flex-shrink-0">{item.timestamp}</span>
                )}
              </div>
              {item.description && (
                <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{item.description}</p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
