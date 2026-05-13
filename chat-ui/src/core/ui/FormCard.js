import { Alert, Form, StatusPill, SurfaceCard } from '../../ui/primitives/index.js';
import { getPrimaryPrimitiveAction, sendPrimitiveResponse } from './workflowPrimitiveUtils.js';

function applyInitialValues(fields, values) {
  if (!Array.isArray(fields)) {
    return [];
  }
  return fields.map((field) => {
    if (!field || typeof field !== 'object') {
      return field;
    }
    const name = field.name;
    if (!name || !values || typeof values !== 'object' || !(name in values)) {
      return field;
    }
    return { ...field, default_value: values[name] };
  });
}

export default function FormCard({ payload = {}, onResponse, onCancel }) {
  const fields = applyInitialValues(payload.fields, payload.values);
  const submitAction = getPrimaryPrimitiveAction(payload, {
    id: payload.submit_action || 'submit',
    label: payload.submit_label || 'Submit',
    variant: 'primary',
  });

  return (
    <SurfaceCard
      title={payload.title || 'Complete the form'}
      subtitle={payload.summary || 'Provide the required details to continue the workflow.'}
      headerAction={<StatusPill label={payload.status || 'ready'} tone="default" />}
    >
      <div className="space-y-4">
        {payload.error ? <Alert message={payload.error} variant="warning" /> : null}

        <Form
          id={payload.component_id || payload.title || 'workflow_form_card'}
          fields={fields}
          layout={payload.layout || 'vertical'}
          columns={payload.columns || 2}
          submit_label={submitAction?.label || payload.submit_label || 'Submit'}
          cancel_label={payload.cancel_label || 'Cancel'}
          onSubmit={async (values) => {
            await sendPrimitiveResponse(onResponse, submitAction || { id: payload.submit_action || 'submit' }, {
              values,
            });
          }}
          onCancel={onCancel ? () => onCancel({ status: 'cancelled', action: 'cancel' }) : undefined}
        />
      </div>
    </SurfaceCard>
  );
}
