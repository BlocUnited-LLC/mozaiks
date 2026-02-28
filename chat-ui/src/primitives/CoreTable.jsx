import React from 'react';
import ArtifactActionsBar from '../components/actions/ArtifactActionsBar';
import { getArtifactArray, getArtifactValue, normalizeActions } from './utils';

const formatValue = (value) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (typeof value === 'object') {
    if (value.label !== undefined) return String(value.label);
    if (value.value !== undefined) return String(value.value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const formatDate = (value) => {
  if (!value) return '';
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return formatValue(value);
  return date.toLocaleDateString();
};

const CoreTable = ({ payload, onAction, actionStatusMap, className = '' }) => {
  const title = getArtifactValue(payload, 'title');
  const columns = getArtifactArray(payload, 'columns');
  const rows = getArtifactArray(payload, 'rows');
  const actions = normalizeActions(getArtifactValue(payload, 'actions', []));
  const rowActions = normalizeActions(getArtifactValue(payload, 'row_actions', []));
  const inlineRowActions = actions.filter(action => (action.scope || 'artifact') === 'row');
  const artifactActions = actions.filter(action => (action.scope || 'artifact') !== 'row');

  const effectiveRowActions = [...rowActions, ...inlineRowActions];
  const hasActionColumn = columns.some(col => (col?.type || col?.key) === 'actions');
  const tableColumns = hasActionColumn || effectiveRowActions.length === 0
    ? columns
    : [...columns, { key: '__actions', label: 'Actions', type: 'actions' }];

  return (
    <div className={`rounded-[var(--core-primitive-radius,16px)] border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))] p-4 space-y-3 ${className}`}>
      {title && (
        <h3 className="text-sm font-semibold text-[var(--core-primitive-text,var(--color-text-primary))]">{title}</h3>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-left text-[var(--core-primitive-muted,var(--color-text-muted))] border-b border-[var(--core-primitive-border,var(--color-border-subtle))]">
              {tableColumns.map((col, idx) => (
                <th key={col?.key || idx} className="py-2 pr-4 font-semibold uppercase text-[10px] tracking-wide">
                  {col?.label || col?.key || 'Column'}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={Math.max(tableColumns.length, 1)}
                  className="py-4 text-xs text-[var(--core-primitive-muted,var(--color-text-muted))]"
                >
                  No rows available.
                </td>
              </tr>
            )}
            {rows.map((row, rowIdx) => (
              <tr
                key={row?.id || rowIdx}
                className="border-b border-[var(--core-primitive-border,var(--color-border-subtle))] last:border-b-0"
              >
                {tableColumns.map((col, colIdx) => {
                  const type = col?.type || 'text';
                  if (type === 'actions') {
                    const contextData = { artifactPayload: payload, row, ...row };
                    return (
                      <td key={`${row?.id || rowIdx}-actions-${colIdx}`} className="py-2 pr-4 align-top">
                        {effectiveRowActions.length > 0 && (
                          <ArtifactActionsBar
                            actions={effectiveRowActions}
                            artifactPayload={payload}
                            contextData={contextData}
                            onAction={onAction}
                            actionStatusMap={actionStatusMap}
                            dense
                            size="sm"
                          />
                        )}
                      </td>
                    );
                  }

                  const value = row ? row[col?.key] : '';
                  const cellValue = type === 'date' ? formatDate(value) : formatValue(value);
                  const alignClass = type === 'number' ? 'text-right' : 'text-left';
                  return (
                    <td key={`${row?.id || rowIdx}-${col?.key || colIdx}`} className={`py-2 pr-4 ${alignClass}`}>
                      {type === 'badge' ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-[rgba(var(--color-primary-rgb),0.2)] text-[var(--core-primitive-text,var(--color-text-primary))]">
                          {cellValue}
                        </span>
                      ) : (
                        <span className="text-[var(--core-primitive-text,var(--color-text-primary))]">{cellValue}</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ArtifactActionsBar
        actions={artifactActions}
        artifactPayload={payload}
        onAction={onAction}
        actionStatusMap={actionStatusMap}
      />
    </div>
  );
};

export default CoreTable;
