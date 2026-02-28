import React from 'react';
import { getArtifactArray, getArtifactValue } from './utils';

const parseGridTemplate = (template) => {
  if (!template || typeof template !== 'string') return null;
  const trimmed = template.trim();
  if (trimmed.includes('x')) {
    const [colsRaw, rowsRaw] = trimmed.split('x');
    const cols = Number(colsRaw);
    const rows = Number(rowsRaw);
    if (cols > 0) {
      return {
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
        gridTemplateRows: rows > 0 ? `repeat(${rows}, minmax(0, auto))` : undefined,
      };
    }
  }
  if (trimmed.includes('-')) {
    const parts = trimmed.split('-').map((part) => Number(part.trim())).filter((n) => n > 0);
    if (parts.length) {
      return { gridTemplateColumns: parts.map((n) => `${n}fr`).join(' ') };
    }
  }
  const numeric = Number(trimmed);
  if (!Number.isNaN(numeric) && numeric > 0) {
    return { gridTemplateColumns: `repeat(${numeric}, minmax(0, 1fr))` };
  }
  return null;
};

const CoreComposite = ({ payload, onAction, actionStatusMap, renderChild, className = '' }) => {
  const layout = getArtifactValue(payload, 'layout', 'stack');
  const gridTemplate = getArtifactValue(payload, 'grid_template');
  const children = getArtifactArray(payload, 'children');

  const gridStyle = layout === 'grid' ? parseGridTemplate(gridTemplate) : null;
  const containerClass = layout === 'columns'
    ? 'grid gap-4 md:grid-flow-col auto-cols-fr'
    : layout === 'grid'
      ? 'grid gap-4'
      : 'flex flex-col gap-4';

  return (
    <div className={`rounded-[var(--core-primitive-radius,16px)] border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))] p-4 ${containerClass} ${className}`} style={gridStyle || undefined}>
      {children.length === 0 && (
        <div className="text-xs text-[var(--core-primitive-muted,var(--color-text-muted))]">No content available.</div>
      )}
      {children.map((child, idx) => (
        <div key={child?.artifact_id || child?.id || idx} className="min-w-0">
          {renderChild
            ? renderChild(child, idx, { onAction, actionStatusMap })
            : (
              <pre className="text-xs text-[var(--core-primitive-text,var(--color-text-primary))] whitespace-pre-wrap">
                {JSON.stringify(child, null, 2)}
              </pre>
            )}
        </div>
      ))}
    </div>
  );
};

export default CoreComposite;
