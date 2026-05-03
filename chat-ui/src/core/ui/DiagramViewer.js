import { Badge, Card } from '../../ui/primitives/index.js';

export default function DiagramViewer({ payload = {} }) {
  const checkpoints = Array.isArray(payload.checkpoints) ? payload.checkpoints : [];
  const legend = Array.isArray(payload.legend) ? payload.legend : [];

  return (
    <Card
      title={payload.title || 'Diagram viewer'}
      subtitle={payload.summary || payload.notes || 'Read-only diagram artifact.'}
      className="border-border/80 bg-card/95 shadow-sm"
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge label="diagram_viewer" variant="secondary" />
          <Badge label={payload.diagram_type || 'artifact'} variant="outline" />
        </div>

        <pre className="overflow-x-auto rounded-md border border-border/60 bg-muted/40 p-4 text-xs leading-6 text-foreground">
          {String(payload.diagram || payload.mermaid_diagram || payload.content || '')}
        </pre>

        {legend.length > 0 ? (
          <div className="rounded-md border border-border/60 bg-background px-3 py-3">
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Legend</p>
            <ul className="mt-2 space-y-1 text-sm text-foreground">
              {legend.map((entry, index) => <li key={`${entry}-${index}`}>{String(entry)}</li>)}
            </ul>
          </div>
        ) : null}

        {checkpoints.length > 0 ? (
          <div className="grid gap-2 sm:grid-cols-3">
            {checkpoints.map((checkpoint, index) => (
              <div key={`${checkpoint?.label || index}`} className="rounded-md border border-border/60 bg-background px-3 py-3">
                <p className="text-sm font-medium text-foreground">{checkpoint?.label || `Checkpoint ${index + 1}`}</p>
                <p className="text-xs uppercase tracking-wide text-muted-foreground">{checkpoint?.status || 'noted'}</p>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </Card>
  );
}
