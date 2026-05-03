// Stat — KPI / metric display primitive for the ui.render event system.
//
// Payload contract:
//   title?  string
//   stats   Array<{
//     label:  string
//     value:  string | number
//     delta?: string          e.g. "+12%" or "-3"
//     trend?: 'up'|'down'|'flat'
//     unit?:  string          e.g. "ms", "%"
//   }>
const TREND_STYLES = {
  up:   'text-success',
  down: 'text-destructive',
  flat: 'text-muted-foreground',
};
const TREND_ICON = { up: '↑', down: '↓', flat: '→' };

export default function Stat({ payload = {} }) {
  const { title, stats = [] } = payload;

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      {title && <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</p>}
      <div className={`grid gap-4 ${stats.length > 1 ? 'grid-cols-2' : 'grid-cols-1'}`}>
        {stats.map((s, i) => (
          <div key={i} className="space-y-1">
            <p className="text-xs text-muted-foreground">{s.label}</p>
            <p className="text-2xl font-bold text-foreground tabular-nums">
              {s.value}{s.unit && <span className="text-base font-normal text-muted-foreground ml-0.5">{s.unit}</span>}
            </p>
            {s.delta && (
              <p className={`text-xs font-medium ${TREND_STYLES[s.trend] || 'text-muted-foreground'}`}>
                {s.trend && TREND_ICON[s.trend]} {s.delta}
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
