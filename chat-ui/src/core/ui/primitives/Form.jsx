// Form — agent-driven generic form primitive for the ui.render event system.
//
// Payload contract:
//   title?       string
//   description? string
//   fields       Array<{
//     key:         string
//     label:       string
//     type:        'text'|'textarea'|'select'|'checkbox'|'number'|'email'|'password'
//     placeholder? string
//     required?    boolean
//     options?     Array<{ label: string, value: string }>  (for select)
//     default?     any
//   }>
//   submit_label? string   default: "Submit"
//   cancel_label? string
import { useState } from 'react';
import { Button } from '../../../ui/base';

function buildInitialValues(fields) {
  const vals = {};
  for (const f of fields) {
    vals[f.key] = f.default !== undefined ? f.default : (f.type === 'checkbox' ? false : '');
  }
  return vals;
}

export default function Form({ payload = {}, onResponse, onCancel }) {
  const {
    title,
    description,
    fields = [],
    submit_label = 'Submit',
    cancel_label,
  } = payload;

  const [values, setValues] = useState(() => buildInitialValues(fields));
  const [submitted, setSubmitted] = useState(false);

  const set = (key, value) => setValues(prev => ({ ...prev, [key]: value }));

  const handleSubmit = (e) => {
    e.preventDefault();
    if (submitted) return;
    setSubmitted(true);
    onResponse?.({ status: 'submitted', values });
  };

  const handleCancel = () => {
    onCancel?.();
    onResponse?.({ status: 'cancelled' });
  };

  const inputBase = 'w-full rounded-md border border-border bg-background text-foreground text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary/40 placeholder:text-muted-foreground disabled:opacity-50';

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-4">
      {title && <p className="text-sm font-semibold text-foreground">{title}</p>}
      {description && <p className="text-sm text-muted-foreground leading-relaxed">{description}</p>}

      <form onSubmit={handleSubmit} className="space-y-3">
        {fields.map((field) => (
          <div key={field.key} className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground block">
              {field.label}
              {field.required && <span className="text-destructive ml-0.5">*</span>}
            </label>

            {field.type === 'textarea' && (
              <textarea
                className={`${inputBase} resize-none h-24`}
                placeholder={field.placeholder}
                required={field.required}
                disabled={submitted}
                value={values[field.key] || ''}
                onChange={e => set(field.key, e.target.value)}
              />
            )}

            {field.type === 'select' && (
              <select
                className={inputBase}
                required={field.required}
                disabled={submitted}
                value={values[field.key] || ''}
                onChange={e => set(field.key, e.target.value)}
              >
                <option value="">{field.placeholder || 'Select…'}</option>
                {(field.options || []).map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            )}

            {field.type === 'checkbox' && (
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  className="rounded border-border text-primary focus:ring-primary/40"
                  disabled={submitted}
                  checked={!!values[field.key]}
                  onChange={e => set(field.key, e.target.checked)}
                />
                <span className="text-sm text-foreground">{field.placeholder || field.label}</span>
              </label>
            )}

            {!['textarea', 'select', 'checkbox'].includes(field.type) && (
              <input
                type={field.type || 'text'}
                className={inputBase}
                placeholder={field.placeholder}
                required={field.required}
                disabled={submitted}
                value={values[field.key] || ''}
                onChange={e => set(field.key, e.target.value)}
              />
            )}
          </div>
        ))}

        <div className="flex gap-2 pt-1">
          <Button type="submit" variant="primary" size="sm" disabled={submitted}>
            {submit_label}
          </Button>
          {cancel_label && (
            <Button type="button" variant="outline" size="sm" disabled={submitted} onClick={handleCancel}>
              {cancel_label}
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
