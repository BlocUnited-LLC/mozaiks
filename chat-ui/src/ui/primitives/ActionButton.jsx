/**
 * ActionButton primitive — single action or labeled button group.
 *
 * Schema properties:
 *   id      {string}   — component id for event routing
 *   title   {string}   — optional label above the buttons
 *   layout  {string}   — "row" | "column" (default: "row")
 *   actions {Action[]} — buttons to render
 *
 * Action schema:
 *   id       {string}
 *   label    {string}
 *   variant  {string}  — button variant
 *   disabled {boolean}
 *   (action_type / href / event_type / workflow_id handled by SectionRenderer)
 *
 * Agent event: ui.actionbutton.update
 *   payload: { component_id, actions }  — replaces actions
 *   payload: { component_id, action_id, disabled }  — toggles a single action
 */

import { useState, useEffect } from 'react';
import { Button } from './Button.jsx';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

export function ActionButton({
  id,
  title,
  layout = 'row',
  actions: initialActions = [],
  onAction,
  className,
}) {
  const [actions, setActions] = useState(initialActions);

  useEffect(() => {
    setActions(Array.isArray(initialActions) ? initialActions : []);
  }, [initialActions]);

  useAppEvent('ui.actionbutton.update', id, (payload) => {
    if (Array.isArray(payload.actions)) {
      setActions(payload.actions);
    } else if (payload.action_id !== undefined) {
      setActions((prev) => prev.map((a) =>
        (a.id ?? a.label) === payload.action_id
          ? { ...a, disabled: payload.disabled ?? a.disabled }
          : a
      ));
    }
  });

  const layoutCls = layout === 'column' ? 'flex-col' : 'flex-row flex-wrap';

  return (
    <div className={cn('space-y-2', className)}>
      {title && <p className="text-sm font-medium text-foreground">{title}</p>}
      <div className={cn('flex gap-2', layoutCls)}>
        {actions.map((action, i) => (
          <Button
            key={action.id ?? i}
            label={action.label}
            variant={action.variant ?? (i === 0 ? 'primary' : 'outline')}
            size={action.size ?? 'default'}
            disabled={action.disabled ?? false}
            onClick={() => onAction?.(action.id ?? action.label, [])}
            className={layout === 'column' ? 'w-full justify-center' : ''}
          />
        ))}
      </div>
    </div>
  );
}
