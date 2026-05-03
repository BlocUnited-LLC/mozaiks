// ProgressTracker — multi-stage progress display primitive for the ui.render event system.
//
// Payload contract:
//   title?   string
//   stages   Array<{
//     label:        string
//     description?: string
//     status:       'done'|'active'|'pending'|'error'
//   }>
const STATUS_STYLES = {
  done:    { ring: 'ring-success bg-success/10 text-success',    label: 'text-foreground' },
  active:  { ring: 'ring-primary bg-primary/10 text-primary',    label: 'text-foreground' },
  pending: { ring: 'ring-border bg-muted text-muted-foreground', label: 'text-muted-foreground' },
  error:   { ring: 'ring-destructive bg-destructive/10 text-destructive', label: 'text-foreground' },
};
const STATUS_ICON = { done: '✓', active: '…', pending: '·', error: '✕' };

export default function ProgressTracker({ payload = {} }) {
  const { title, stages = [] } = payload;

  const doneCount = stages.filter(s => s.status === 'done').length;
  const totalCount = stages.length;
  const pct = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-4">
      {/* Title + overall progress */}
      <div className="flex items-center justify-between gap-2">
        {title && <p className="text-sm font-semibold text-foreground">{title}</p>}
        <span className="text-xs text-muted-foreground ml-auto">{doneCount}/{totalCount}</span>
      </div>

      {/* Progress bar */}
      {totalCount > 0 && (
        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}

      {/* Stage list */}
      <div className="space-y-2">
        {stages.map((stage, i) => {
          const s = STATUS_STYLES[stage.status] || STATUS_STYLES.pending;
          return (
            <div key={i} className="flex items-start gap-3">
              <div className={`w-6 h-6 rounded-full ring-2 flex items-center justify-center flex-shrink-0 text-[11px] font-bold ${s.ring}`}>
                {STATUS_ICON[stage.status] || '·'}
              </div>
              <div className="flex-1 min-w-0 pt-0.5">
                <p className={`text-sm font-medium ${s.label}`}>{stage.label}</p>
                {stage.description && (
                  <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{stage.description}</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
