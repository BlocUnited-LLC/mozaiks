import React from 'react';
import ArtifactActionsBar from '../components/actions/ArtifactActionsBar';
import { getArtifactArray, getArtifactValue, normalizeActions } from './utils';

const isImageLike = (value) =>
  typeof value === 'string' && (value.startsWith('http://') || value.startsWith('https://') || value.startsWith('/'));

const CoreList = ({ payload, onAction, actionStatusMap, className = '' }) => {
  const title = getArtifactValue(payload, 'title');
  const items = getArtifactArray(payload, 'items');
  const actions = normalizeActions(getArtifactValue(payload, 'actions', []));
  const rowScopedActions = actions.filter(action => (action.scope || 'artifact') === 'row');
  const artifactActions = actions.filter(action => (action.scope || 'artifact') !== 'row');

  return (
    <div className={`rounded-[var(--core-primitive-radius,16px)] border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))] p-4 space-y-3 ${className}`}>
      {title && (
        <h3 className="text-sm font-semibold text-[var(--core-primitive-text,var(--color-text-primary))]">{title}</h3>
      )}
      <div className="space-y-2">
        {items.length === 0 && (
          <div className="text-xs text-[var(--core-primitive-muted,var(--color-text-muted))]">No items available.</div>
        )}
        {items.map((item, idx) => {
          const itemActions = normalizeActions(item?.actions || []);
          const combinedActions = [...rowScopedActions, ...itemActions];
          const contextData = { artifactPayload: payload, item, ...item };
          return (
            <div
              key={item?.id || idx}
              className="flex flex-col gap-2 rounded-lg border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface-alt,var(--color-surface-alt,var(--color-surface)))] px-3 py-2"
            >
              <div className="flex items-start gap-3">
                {item?.icon && (
                  isImageLike(item.icon) ? (
                    <img src={item.icon} alt="" className="w-8 h-8 rounded-lg object-cover" />
                  ) : (
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-[rgba(var(--color-primary-rgb),0.15)] text-sm">
                      {item.icon}
                    </div>
                  )
                )}
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-[var(--core-primitive-text,var(--color-text-primary))] truncate">
                    {item?.title || 'Untitled'}
                  </div>
                  {item?.subtitle && (
                    <div className="text-xs text-[var(--core-primitive-muted,var(--color-text-muted))] truncate">
                      {item.subtitle}
                    </div>
                  )}
                </div>
              </div>
              {combinedActions.length > 0 && (
                <ArtifactActionsBar
                  actions={combinedActions}
                  artifactPayload={payload}
                  contextData={contextData}
                  onAction={onAction}
                  actionStatusMap={actionStatusMap}
                  dense
                  size="sm"
                />
              )}
            </div>
          );
        })}
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

export default CoreList;
