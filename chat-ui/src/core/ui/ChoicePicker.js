import { useMemo, useState } from 'react';
import { Alert, Button, StatusPill, SurfaceCard } from '../../ui/primitives/index.js';
import { normalizeOptions, normalizePrimitiveActions, sendPrimitiveResponse } from './workflowPrimitiveUtils.js';

export default function ChoicePicker({ payload = {}, onResponse, onCancel }) {
  const selectionMode = String(payload.selection_mode || payload.selectionMode || 'single');
  const options = useMemo(() => normalizeOptions(payload.options), [payload.options]);
  const [selected, setSelected] = useState(selectionMode === 'multi' ? [] : '');
  const [submitting, setSubmitting] = useState(false);
  const actions = normalizePrimitiveActions(payload, [
    { id: payload.submit_action || 'submit_selection', label: payload.submit_label || 'Continue', variant: 'primary' },
  ]);

  const toggle = (option) => {
    if (selectionMode === 'multi') {
      setSelected((current) => (
        current.includes(option.id)
          ? current.filter((value) => value !== option.id)
          : [...current, option.id]
      ));
      return;
    }
    setSelected(option.id);
  };

  const submit = async (action) => {
    setSubmitting(true);
    try {
      const selection = selectionMode === 'multi' ? selected : (selected ? [selected] : []);
      const selectedOptions = options.filter((option) => selection.includes(option.id));
      await sendPrimitiveResponse(onResponse, action, {
        selection,
        selected_options: selectedOptions,
        value: selectionMode === 'multi' ? selection : (selection[0] || null),
      });
    } finally {
      setSubmitting(false);
    }
  };

  const hasSelection = Array.isArray(selected) ? selected.length > 0 : Boolean(selected);

  return (
    <SurfaceCard
      title={payload.title || 'Choose an option'}
      subtitle={payload.summary || 'Pick one or more options to continue the workflow.'}
      headerAction={<StatusPill label={selectionMode === 'multi' ? 'multi-select' : 'single-select'} tone="default" />}
    >
      <div className="space-y-4">
        {payload.error ? <Alert message={payload.error} variant="warning" /> : null}

        <div className="space-y-2">
          {options.map((option) => {
            const isSelected = Array.isArray(selected) ? selected.includes(option.id) : selected === option.id;
            return (
              <button
                key={option.id}
                type="button"
                disabled={option.disabled || submitting}
                onClick={() => toggle(option)}
                className={`w-full rounded-md border px-3 py-3 text-left transition ${
                  isSelected
                    ? 'border-primary bg-primary/10 text-foreground'
                    : 'border-border/60 bg-background text-foreground hover:border-primary/60'
                } ${option.disabled ? 'cursor-not-allowed opacity-60' : ''}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium">{option.label}</span>
                  {isSelected ? <StatusPill label="Selected" tone="success" /> : null}
                </div>
                {option.description ? (
                  <p className="mt-1 text-sm text-muted-foreground">{option.description}</p>
                ) : null}
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap gap-3">
          {actions.map((action) => (
            <Button
              key={action.id}
              label={submitting ? 'Submitting…' : action.label}
              variant={action.variant}
              disabled={!hasSelection || submitting}
              onClick={() => submit(action)}
            />
          ))}
          {onCancel ? (
            <Button label="Cancel" variant="ghost" disabled={submitting} onClick={() => onCancel({ status: 'cancelled', action: 'cancel' })} />
          ) : null}
        </div>
      </div>
    </SurfaceCard>
  );
}
