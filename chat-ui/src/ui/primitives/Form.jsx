/**
 * Form primitive — declarative form with validation and agent-controlled fields.
 *
 * Schema properties:
 *   id           {string}    — component id for event routing
 *   fields       {Field[]}   — field definitions (see below)
 *   layout       {string}    — "vertical" | "horizontal" | "grid"
 *   columns      {number}    — column count when layout=grid (default 2)
 *   submit_label {string}    — submit button text (default "Submit")
 *   onSubmit     {Function}  — (values: Record<string, any>) => void
 *   onCancel     {Function}
 *   cancel_label {string}
 *   disabled     {boolean}
 *
 * Field schema:
 *   name         {string}
 *   label        {string}
 *   type         {string}    — "text" | "email" | "password" | "number" | "textarea" | "select" | "checkbox"
 *   required     {boolean}
 *   placeholder  {string}
 *   default_value {any}
 *   options      {Array<{value, label}>}  — for select
 *
 * Agent event: ui.form.set_field
 *   payload: { component_id, field, value }
 */

import { useState, useCallback } from 'react';
import { Input } from '../base/components/input.jsx';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '../base/components/select.jsx';
import { Button } from './Button.jsx';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

const GRID_COL_CLASS = {
  1: 'grid-cols-1',
  2: 'grid-cols-1 sm:grid-cols-2',
  3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
  4: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4',
  5: 'grid-cols-1 sm:grid-cols-3 lg:grid-cols-5',
  6: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-6',
};

function buildInitialValues(fields) {
  return Object.fromEntries(
    fields.map((f) => [f.name, f.default_value ?? (f.type === 'checkbox' ? false : '')])
  );
}

function FieldRenderer({ field, value, onChange, disabled, fieldId }) {
  const baseClass = 'w-full';
  switch (field.type) {
    case 'textarea':
      return (
        <textarea
          id={fieldId}
          className={cn(
            baseClass,
            'min-h-[120px] rounded-[var(--shell-control-radius,1rem)] border border-input bg-transparent px-4 py-3 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50'
          )}
          placeholder={field.placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          required={field.required}
        />
      );
    case 'select':
      return (
        <Select value={value} onValueChange={onChange} disabled={disabled}>
          <SelectTrigger id={fieldId} className={cn(baseClass, 'h-11 rounded-[var(--shell-control-radius,1rem)] px-4')}>
            <SelectValue placeholder={field.placeholder ?? `Select ${field.label}`} />
          </SelectTrigger>
          <SelectContent>
            {(field.options ?? []).map((opt) => (
              <SelectItem key={opt.value} value={String(opt.value)}>{opt.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    case 'checkbox':
      return (
        <div className="flex items-center gap-2">
          <input
            id={fieldId}
            type="checkbox"
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
            disabled={disabled}
            className="h-4 w-4 rounded border border-input"
          />
          {field.placeholder && <span className="text-sm text-muted-foreground">{field.placeholder}</span>}
        </div>
      );
    default:
      return (
        <Input
          id={fieldId}
          type={field.type ?? 'text'}
          placeholder={field.placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          required={field.required}
          className={cn(baseClass, 'h-11 rounded-[var(--shell-control-radius,1rem)] px-4')}
        />
      );
  }
}

export function Form({
  id,
  fields = [],
  layout = 'vertical',
  columns = 2,
  submit_label = 'Submit',
  onSubmit,
  onCancel,
  cancel_label = 'Cancel',
  disabled = false,
  className,
}) {
  const [values, setValues]  = useState(() => buildInitialValues(fields));
  const [errors, setErrors]  = useState({});
  const [loading, setLoading] = useState(false);

  const setField = useCallback((name, value) => {
    setValues((prev) => ({ ...prev, [name]: value }));
    setErrors((prev) => ({ ...prev, [name]: undefined }));
  }, []);

  // Agent-controlled field updates
  useAppEvent('ui.form.set_field', id, ({ field, value }) => {
    if (field) setField(field, value);
  });

  const validate = () => {
    const newErrors = {};
    for (const f of fields) {
      if (f.required && !values[f.name] && values[f.name] !== false) {
        newErrors[f.name] = `${f.label} is required`;
      }
    }
    return newErrors;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const newErrors = validate();
    if (Object.keys(newErrors).length > 0) { setErrors(newErrors); return; }
    setLoading(true);
    try { await onSubmit?.(values); }
    finally { setLoading(false); }
  };

  const resolvedColumns = Number(columns) || 2;
  const gridClass = layout === 'grid'
    ? cn('grid gap-4', GRID_COL_CLASS[resolvedColumns] ?? GRID_COL_CLASS[2])
    : 'space-y-4';

  return (
    <form onSubmit={handleSubmit} className={cn(className)} noValidate>
      <div className={gridClass}>
        {fields.map((field) => {
          const fieldId = `${id || 'form'}-${field.name}`.replace(/[^A-Za-z0-9_-]/g, '-');
          return (
            <div key={field.name} className="space-y-1.5">
              <label htmlFor={fieldId} className="text-sm font-medium text-foreground">
                {field.label}
                {field.required && <span className="text-destructive ml-1">*</span>}
              </label>
              <FieldRenderer
                field={field}
                fieldId={fieldId}
                value={values[field.name]}
                onChange={(val) => setField(field.name, val)}
                disabled={disabled || loading}
              />
              {errors[field.name] && (
                <p className="text-xs text-destructive">{errors[field.name]}</p>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-6 flex flex-col-reverse gap-3 border-t border-border/60 pt-4 sm:flex-row sm:items-center sm:justify-end">
        <Button
          type="submit"
          label={loading ? 'Saving…' : submit_label}
          variant="primary"
          className="w-full sm:w-auto"
          disabled={disabled || loading}
        />
        {onCancel && (
          <Button
            type="button"
            label={cancel_label}
            variant="ghost"
            onClick={onCancel}
            className="w-full sm:w-auto"
            disabled={loading}
          />
        )}
      </div>
    </form>
  );
}
