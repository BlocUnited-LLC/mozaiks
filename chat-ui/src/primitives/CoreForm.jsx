import React from 'react';
import { getArtifactArray, getArtifactValue } from './utils';

const getInitialValues = (fields) => {
  const initial = {};
  fields.forEach((field) => {
    if (!field || !field.name) return;
    if (field.value !== undefined) {
      initial[field.name] = field.value;
    } else if (field.default !== undefined) {
      initial[field.name] = field.default;
    } else if (field.type === 'checkbox') {
      initial[field.name] = false;
    } else {
      initial[field.name] = '';
    }
  });
  return initial;
};

const normalizeOptions = (options = []) => {
  if (!Array.isArray(options)) return [];
  return options.map((opt) => {
    if (typeof opt === 'string' || typeof opt === 'number') {
      return { label: String(opt), value: opt };
    }
    if (opt && typeof opt === 'object') {
      return {
        label: opt.label ?? opt.value ?? '',
        value: opt.value ?? opt.label ?? '',
      };
    }
    return { label: '', value: '' };
  });
};

const CoreForm = ({ payload, onAction, actionStatusMap, className = '' }) => {
  const title = getArtifactValue(payload, 'title');
  const fields = getArtifactArray(payload, 'fields');
  const submitAction = getArtifactValue(payload, 'submit_action') || getArtifactValue(payload, 'submitAction');
  const cancelAction = getArtifactValue(payload, 'cancel_action') || getArtifactValue(payload, 'cancelAction');

  const [values, setValues] = React.useState(() => getInitialValues(fields));
  const [errors, setErrors] = React.useState({});
  const [pendingSubmitId, setPendingSubmitId] = React.useState(null);
  const [pendingCancelId, setPendingCancelId] = React.useState(null);

  React.useEffect(() => {
    setValues(getInitialValues(fields));
    setErrors({});
  }, [fields]);

  const submitStatus = pendingSubmitId ? actionStatusMap?.[pendingSubmitId] : null;
  const cancelStatus = pendingCancelId ? actionStatusMap?.[pendingCancelId] : null;
  const isSubmitting = submitStatus ? ['pending', 'started'].includes(submitStatus.status) : false;
  const isCancelling = cancelStatus ? ['pending', 'started'].includes(cancelStatus.status) : false;

  React.useEffect(() => {
    if (pendingSubmitId && submitStatus && !['pending', 'started'].includes(submitStatus.status)) {
      setPendingSubmitId(null);
    }
  }, [pendingSubmitId, submitStatus]);

  React.useEffect(() => {
    if (pendingCancelId && cancelStatus && !['pending', 'started'].includes(cancelStatus.status)) {
      setPendingCancelId(null);
    }
  }, [pendingCancelId, cancelStatus]);

  const handleChange = (name, value) => {
    setValues((prev) => ({ ...prev, [name]: value }));
  };

  const validate = () => {
    const nextErrors = {};
    fields.forEach((field) => {
      if (!field || !field.name) return;
      if (field.required) {
        const value = values[field.name];
        if (field.type === 'checkbox') {
          if (!value) nextErrors[field.name] = 'Required';
        } else if (value === null || value === undefined || String(value).trim() === '') {
          nextErrors[field.name] = 'Required';
        }
      }
    });
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!submitAction || !onAction) return;
    if (!validate()) return;
    const contextData = { artifactPayload: payload, form_data: values, ...values };
    const actionId = onAction(submitAction, contextData);
    if (actionId) setPendingSubmitId(actionId);
  };

  const handleCancel = () => {
    if (!cancelAction || !onAction) return;
    const contextData = { artifactPayload: payload, form_data: values, ...values };
    const actionId = onAction(cancelAction, contextData);
    if (actionId) setPendingCancelId(actionId);
  };

  return (
    <form
      onSubmit={handleSubmit}
      className={`rounded-[var(--core-primitive-radius,16px)] border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))] p-4 space-y-4 ${className}`}
    >
      {title && (
        <h3 className="text-sm font-semibold text-[var(--core-primitive-text,var(--color-text-primary))]">{title}</h3>
      )}
      <div className="space-y-3">
        {fields.map((field, idx) => {
          const fieldId = field?.name || `field-${idx}`;
          const fieldType = field?.type || 'text';
          const value = values[fieldId] ?? '';
          const error = errors[fieldId];
          const options = normalizeOptions(field?.options || []);
          const isStandardInput = !['textarea', 'select', 'checkbox'].includes(fieldType);
          const inputType = ['text', 'number', 'date'].includes(fieldType) ? fieldType : 'text';

          const inputClass = `w-full rounded-lg border px-3 py-2 text-sm bg-[var(--core-primitive-surface-alt,var(--color-surface-alt,var(--color-surface)))] text-[var(--core-primitive-text,var(--color-text-primary))] border-[var(--core-primitive-border,var(--color-border-subtle))] focus:outline-none focus:ring-2 focus:ring-[rgba(var(--color-primary-rgb),0.35)]`;
          return (
            <div key={fieldId} className="space-y-1">
              <label className="text-xs font-semibold uppercase tracking-wide text-[var(--core-primitive-muted,var(--color-text-muted))]">
                {field?.label || fieldId}
                {field?.required && <span className="ml-1 text-[var(--color-error)]">*</span>}
              </label>
              {fieldType === 'textarea' && (
                <textarea
                  value={value}
                  onChange={(event) => handleChange(fieldId, event.target.value)}
                  rows={field?.rows || 4}
                  placeholder={field?.placeholder || ''}
                  className={inputClass}
                />
              )}
              {fieldType === 'select' && (
                <select
                  value={value}
                  onChange={(event) => handleChange(fieldId, event.target.value)}
                  className={inputClass}
                >
                  {!field?.required && <option value="">Select...</option>}
                  {options.map((opt, optIdx) => (
                    <option key={`${opt.value}-${optIdx}`} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              )}
              {fieldType === 'checkbox' && (
                <label className="flex items-center gap-2 text-sm text-[var(--core-primitive-text,var(--color-text-primary))]">
                  <input
                    type="checkbox"
                    checked={Boolean(value)}
                    onChange={(event) => handleChange(fieldId, event.target.checked)}
                    className="h-4 w-4 rounded border border-[var(--core-primitive-border,var(--color-border-subtle))]"
                  />
                  {field?.help || field?.description || 'Yes'}
                </label>
              )}
              {isStandardInput && (
                <input
                  type={inputType}
                  value={value}
                  onChange={(event) => handleChange(fieldId, event.target.value)}
                  placeholder={field?.placeholder || ''}
                  className={inputClass}
                />
              )}
              {error && (
                <div className="text-xs text-[var(--color-error)]">{error}</div>
              )}
              {(field?.help || field?.description) && fieldType !== 'checkbox' && (
                <div className="text-xs text-[var(--core-primitive-muted,var(--color-text-muted))]">
                  {field.help || field.description}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap gap-2 pt-2">
        {cancelAction && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={isCancelling}
            className={`rounded-lg border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-[var(--core-primitive-text,var(--color-text-primary))] border-[var(--core-primitive-border,var(--color-border-subtle))] ${
              isCancelling ? 'opacity-60 cursor-wait' : 'hover:border-[var(--color-primary)]'
            }`}
          >
            {isCancelling ? 'Working...' : (cancelAction.label || 'Cancel')}
          </button>
        )}
        {submitAction && (
          <button
            type="submit"
            disabled={isSubmitting}
            className={`rounded-lg px-4 py-2 text-xs font-semibold uppercase tracking-wide text-white bg-[var(--color-primary)] ${
              isSubmitting ? 'opacity-60 cursor-wait' : 'hover:brightness-110'
            }`}
          >
            {isSubmitting ? 'Submitting...' : (submitAction.label || 'Submit')}
          </button>
        )}
      </div>
    </form>
  );
};

export default CoreForm;
