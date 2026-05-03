import { useState } from 'react';
import { Alert, Badge, Button, Card } from '../../ui/primitives/index.js';
import { normalizeActions, sendPrimitiveResponse } from './workflowPrimitiveUtils.js';

const fallbackActions = [
  { id: 'approve', label: 'Approve', variant: 'primary', approved: true },
  { id: 'request_changes', label: 'Request Changes', variant: 'secondary', approved: false },
];

export default function ApprovalCard({ payload = {}, onResponse, onCancel, workflowName }) {
  const [rationale, setRationale] = useState('');
  const [submittingAction, setSubmittingAction] = useState('');
  const actions = normalizeActions(payload.actions, fallbackActions);
  const checkpoints = Array.isArray(payload.checkpoints) ? payload.checkpoints : payload.items || [];

  const submit = async (action) => {
    setSubmittingAction(action.id);
    try {
      await sendPrimitiveResponse(onResponse, action, {
        rationale: rationale.trim(),
        workflow_name: workflowName,
      });
    } finally {
      setSubmittingAction('');
    }
  };

  return (
    <Card
      title={payload.title || 'Approval required'}
      subtitle={payload.summary || 'Review the details below and choose how the workflow should proceed.'}
      className="border-border/80 bg-card/95 shadow-sm"
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge label="approval_card" variant="secondary" />
          <Badge label={payload.status || 'ready'} variant="outline" />
        </div>

        {payload.error ? <Alert message={payload.error} variant="warning" /> : null}

        {checkpoints.length > 0 ? (
          <ul className="space-y-2">
            {checkpoints.map((checkpoint, index) => (
              <li key={`${index}-${String(checkpoint)}`} className="rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-sm text-foreground">
                {typeof checkpoint === 'string'
                  ? checkpoint
                  : checkpoint?.label || checkpoint?.title || checkpoint?.summary || `Checkpoint ${index + 1}`}
              </li>
            ))}
          </ul>
        ) : null}

        {payload.capture_rationale !== false ? (
          <label className="block space-y-2">
            <span className="text-sm font-medium text-foreground">Optional rationale</span>
            <textarea
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              rows={3}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground shadow-sm outline-none focus:border-primary"
              placeholder={payload.rationale_placeholder || 'Add context for your decision.'}
            />
          </label>
        ) : null}

        <div className="flex flex-wrap gap-3">
          {actions.map((action) => (
            <Button
              key={action.id}
              label={submittingAction === action.id ? 'Submitting…' : action.label}
              variant={action.variant}
              disabled={Boolean(submittingAction)}
              onClick={() => submit(action)}
            />
          ))}
          {onCancel ? (
            <Button
              label="Cancel"
              variant="ghost"
              disabled={Boolean(submittingAction)}
              onClick={() => onCancel({ status: 'cancelled', action: 'cancel' })}
            />
          ) : null}
        </div>
      </div>
    </Card>
  );
}
