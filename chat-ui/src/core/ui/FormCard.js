import { Alert, Badge, Card, Form } from '../../ui/primitives/index.js';

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

  return (
    <Card
      title={payload.title || 'Complete the form'}
      subtitle={payload.summary || 'Provide the required details to continue the workflow.'}
      className="border-border/80 bg-card/95 shadow-sm"
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge label="form_card" variant="secondary" />
          <Badge label={payload.status || 'ready'} variant="outline" />
        </div>

        {payload.error ? <Alert message={payload.error} variant="warning" /> : null}

        <Form
          id={payload.component_id || payload.title || 'workflow_form_card'}
          fields={fields}
          layout={payload.layout || 'vertical'}
          columns={payload.columns || 2}
          submit_label={payload.submit_label || 'Submit'}
          cancel_label={payload.cancel_label || 'Cancel'}
          onSubmit={async (values) => {
            await onResponse?.({
              status: 'submitted',
              action: payload.submit_action || 'submit',
              values,
            });
          }}
          onCancel={onCancel ? () => onCancel({ status: 'cancelled', action: 'cancel' }) : undefined}
        />
      </div>
    </Card>
  );
}
