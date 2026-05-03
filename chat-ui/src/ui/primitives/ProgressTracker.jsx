/**
 * ProgressTracker primitive — multi-stage progress display with overall bar.
 *
 * Schema properties:
 *   id      {string}   — component id for event routing
 *   title   {string}   — optional heading
 *   stages  {Stage[]}  — ordered list of stages
 *
 * Stage schema:
 *   label        {string}
 *   description  {string}
 *   status       {string}  — "done" | "active" | "pending" | "error"
 *
 * Agent event: ui.progress.update
 *   payload: { component_id, stages }  — replaces stages array
 *   payload: { component_id, stage_index, status }  — updates a single stage
 */

import { useState, useEffect } from 'react';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

const RING = {
  done:    'ring-success bg-success/10 text-success',
  active:  'ring-primary bg-primary/10 text-primary',
  pending: 'ring-border bg-muted text-muted-foreground',
  error:   'ring-destructive bg-destructive/10 text-destructive',
};
const LABEL = {
  done: 'text-foreground', active: 'text-foreground', pending: 'text-muted-foreground', error: 'text-foreground',
};
const ICON = { done: '✓', active: '…', pending: '·', error: '✕' };

export function ProgressTracker({ id, title, stages: initialStages = [], className }) {
  const [stages, setStages] = useState(initialStages);

  useEffect(() => {
    setStages(Array.isArray(initialStages) ? initialStages : []);
  }, [initialStages]);

  useAppEvent('ui.progress.update', id, (payload) => {
    if (Array.isArray(payload.stages)) {
      setStages(payload.stages);
    } else if (typeof payload.stage_index === 'number' && payload.status) {
      setStages((prev) => prev.map((s, i) =>
        i === payload.stage_index ? { ...s, status: payload.status } : s
      ));
    }
  });

  const doneCount = stages.filter((s) => s.status === 'done').length;
  const pct = stages.length > 0 ? Math.round((doneCount / stages.length) * 100) : 0;

  return (
    <div className={cn('space-y-4', className)}>
      {(title || stages.length > 0) && (
        <div className="flex items-center justify-between gap-2">
          {title && <p className="text-sm font-semibold text-foreground">{title}</p>}
          <span className="text-xs text-muted-foreground ml-auto">{doneCount}/{stages.length}</span>
        </div>
      )}
      {stages.length > 0 && (
        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
      <div className="space-y-2">
        {stages.map((stage, i) => {
          const ring   = RING[stage.status]  ?? RING.pending;
          const labelC = LABEL[stage.status] ?? LABEL.pending;
          return (
            <div key={i} className="flex items-start gap-3">
              <div className={cn('w-6 h-6 rounded-full ring-2 flex items-center justify-center flex-shrink-0 text-[11px] font-bold', ring)}>
                {ICON[stage.status] ?? '·'}
              </div>
              <div className="flex-1 min-w-0 pt-0.5">
                <p className={cn('text-sm font-medium', labelC)}>{stage.label}</p>
                {stage.description && (
                  <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{stage.description}</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
