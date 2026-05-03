import { Badge, Button, Card } from '../../ui/primitives/index.js';
import { normalizeActions, sendPrimitiveResponse } from './workflowPrimitiveUtils.js';

const fallbackActions = [
  { id: 'download_accepted', label: 'Done', variant: 'primary', approved: true },
  { id: 'close', label: 'Close', variant: 'secondary' },
];

function formatFileSize(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size <= 0) {
    return null;
  }
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (size >= 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${size} B`;
}

export default function DownloadCenter({ payload = {}, onResponse, onCancel }) {
  const files = Array.isArray(payload.files) ? payload.files : [];
  const actions = normalizeActions(payload.actions, onResponse ? fallbackActions : []);

  return (
    <Card
      title={payload.title || 'Download center'}
      subtitle={payload.summary || 'Review the generated files and finish the workflow when ready.'}
      className="border-border/80 bg-card/95 shadow-sm"
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge label="download_center" variant="secondary" />
          <Badge label={`${files.length} file${files.length === 1 ? '' : 's'}`} variant="outline" />
        </div>

        <div className="space-y-2">
          {files.map((file, index) => (
            <div key={`${file?.name || index}`} className="rounded-md border border-border/60 bg-muted/30 px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-foreground">{file?.name || `File ${index + 1}`}</p>
                  {file?.description ? (
                    <p className="text-sm text-muted-foreground">{String(file.description)}</p>
                  ) : null}
                </div>
                <div className="text-right text-xs text-muted-foreground">
                  {file?.status ? <p>{String(file.status)}</p> : null}
                  {formatFileSize(file?.size) ? <p>{formatFileSize(file?.size)}</p> : null}
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap gap-3">
          {actions.map((action) => (
            <Button
              key={action.id}
              label={action.label}
              variant={action.variant}
              onClick={() => sendPrimitiveResponse(onResponse, action, { files })}
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
