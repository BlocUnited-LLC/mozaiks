import { Grid } from './Grid.jsx';
import { SurfaceCard } from './Surface.jsx';

function readPath(source, path) {
  if (!source || !path) return undefined;
  return String(path)
    .split('.')
    .filter(Boolean)
    .reduce((acc, key) => (acc && typeof acc === 'object' ? acc[key] : undefined), source);
}

function resolveSubtitle(row, subtitlePaths = []) {
  for (const path of subtitlePaths) {
    const value = readPath(row, path);
    if (value) return value;
  }
  return '';
}

export function PerformanceTileGrid({
  title,
  subtitle,
  rows = [],
  limit = 3,
  eyebrowPath = 'kind',
  titlePath = 'title',
  subtitlePaths = ['summary'],
  fallbackSubtitle = 'Performance entity',
  keyPath = 'id',
  stats = [],
}) {
  const scopedRows = (Array.isArray(rows) ? rows : []).slice(0, Math.max(1, Number(limit) || 3));
  if (!scopedRows.length) return null;

  return (
    <section className="space-y-4">
      {(title || subtitle) ? (
        <div>
          {title ? <h2 className="text-lg font-semibold tracking-[-0.02em] text-foreground">{title}</h2> : null}
          {subtitle ? <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p> : null}
        </div>
      ) : null}
      <Grid columns={3} gap="4">
        {scopedRows.map((row, index) => {
          const tileTitle = readPath(row, titlePath) || `Item ${index + 1}`;
          const tileEyebrow = readPath(row, eyebrowPath) || undefined;
          const tileSubtitle = resolveSubtitle(row, subtitlePaths) || fallbackSubtitle;
          const key = readPath(row, keyPath) || `${tileTitle}-${index}`;
          return (
            <SurfaceCard key={key} eyebrow={tileEyebrow} title={tileTitle} subtitle={tileSubtitle}>
              <div className="space-y-1 text-sm text-muted-foreground">
                {stats.map((stat) => (
                  <div key={stat.label}>
                    {stat.label}: {readPath(row, stat.path) ?? stat.fallback ?? 0}
                  </div>
                ))}
              </div>
            </SurfaceCard>
          );
        })}
      </Grid>
    </section>
  );
}

export default PerformanceTileGrid;
