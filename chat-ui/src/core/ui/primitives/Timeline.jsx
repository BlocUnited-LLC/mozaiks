// Timeline — ordered event/step list primitive for the ui.render event system.
//
// Payload contract:
//   title?  string
//   items   Array<{
//     label:        string
//     description?: string
//     status:       'done'|'active'|'pending'|'error'
//     timestamp?:   string
//   }>
const STATUS_STYLES = {
  done:    { dot: 'bg-success border-success',      text: 'text-success' },
  active:  { dot: 'bg-primary border-primary animate-pulse', text: 'text-primary' },
  pending: { dot: 'bg-muted border-border',         text: 'text-muted-foreground' },
  error:   { dot: 'bg-destructive border-destructive', text: 'text-destructive' },
};
const STATUS_ICON = { done: '✓', active: '●', pending: '○', error: '✕' };

export default function Timeline({ payload = {} }) {
  const { title, items = [] } = payload;

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      {title && <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</p>}
      <div className="relative space-y-0">
        {items.map((item, i) => {
          const s = STATUS_STYLES[item.status] || STATUS_STYLES.pending;
          const isLast = i === items.length - 1;
          return (
            <div key={i} className="flex gap-3">
              {/* Spine column */}
              <div className="flex flex-col items-center">
                <div className={`w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 text-[10px] font-bold text-white ${s.dot}`}>
                  {STATUS_ICON[item.status] || '○'}
                </div>
                {!isLast && <div className="w-px flex-1 bg-border min-h-[16px]" />}
              </div>
              {/* Content column */}
              <div className={`pb-4 flex-1 min-w-0 ${isLast ? 'pb-0' : ''}`}>
                <div className="flex items-baseline justify-between gap-2 flex-wrap">
                  <p className="text-sm font-medium text-foreground">{item.label}</p>
                  {item.timestamp && (
                    <span className="text-xs text-muted-foreground flex-shrink-0">{item.timestamp}</span>
                  )}
                </div>
                {item.description && (
                  <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{item.description}</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
