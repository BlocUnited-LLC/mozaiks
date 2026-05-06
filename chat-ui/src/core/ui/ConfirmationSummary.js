import { Alert, Badge, Button, Card } from '../../ui/primitives/index.js';
import { normalizePrimitiveActions, normalizeSummaryItems, sendPrimitiveResponse } from './workflowPrimitiveUtils.js';

const fallbackActions = [
  { id: 'confirm', label: 'Confirm', variant: 'primary', approved: true },
  { id: 'revise', label: 'Revise', variant: 'secondary', approved: false },
];

export default function ConfirmationSummary({ payload = {}, onResponse, onCancel }) {
  const items = normalizeSummaryItems(payload.items || payload.fields);
  const actions = normalizePrimitiveActions(payload, fallbackActions);

  return (
    <Card
      title={payload.title || 'Confirm the summary'}
      subtitle={payload.summary || 'Review the captured details before continuing.'}
      className="border-border/80 bg-card/95 shadow-sm"
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge label="confirmation_summary" variant="secondary" />
          <Badge label={payload.status || 'review'} variant="outline" />
        </div>

        {payload.error ? <Alert message={payload.error} variant="warning" /> : null}

        <div className="space-y-2">
          {items.map((item) => (
            <div key={item.id} className="rounded-md border border-border/60 bg-muted/30 px-3 py-3">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">{item.label}</p>
              <p className="mt-1 text-sm text-foreground">{String(item.value || '—')}</p>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap gap-3">
          {actions.map((action) => (
            <Button
              key={action.id}
              label={action.label}
              variant={action.variant}
              onClick={() => sendPrimitiveResponse(onResponse, action, { items })}
            />
          ))}
          {onCancel ? (
            <Button label="Cancel" variant="ghost" onClick={() => onCancel({ status: 'cancelled', action: 'cancel' })} />
          ) : null}
        </div>
      </div>
    </Card>
  );
}
