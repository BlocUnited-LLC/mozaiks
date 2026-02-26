import React, { useEffect, useMemo, useState } from 'react';

const STYLE_CLASSES = {
  primary: 'bg-[var(--color-primary)] text-white border-[rgba(var(--color-primary-light-rgb),0.4)] hover:border-[rgba(var(--color-primary-light-rgb),0.8)] hover:brightness-110',
  secondary: 'bg-[rgba(var(--color-secondary-rgb),0.2)] text-white border-[rgba(var(--color-secondary-rgb),0.35)] hover:border-[rgba(var(--color-secondary-rgb),0.7)]',
  ghost: 'bg-transparent text-white/80 border-white/20 hover:border-white/50 hover:text-white',
  danger: 'bg-[rgba(239,68,68,0.2)] text-white border-[rgba(239,68,68,0.45)] hover:border-[rgba(239,68,68,0.75)]'
};

const SIZE_CLASSES = {
  sm: 'px-2 py-1 text-[10px]',
  md: 'px-3 py-1.5 text-xs',
  lg: 'px-4 py-2 text-sm'
};

const normalizeActions = (actions) =>
  Array.isArray(actions) ? actions.filter(Boolean).filter(a => a.tool) : [];

const ActionButton = ({ action, contextData, onAction, actionStatusMap, size = 'md' }) => {
  const [pendingActionId, setPendingActionId] = useState(null);
  const status = pendingActionId ? actionStatusMap?.[pendingActionId] : null;
  const isLoading = status ? ['pending', 'started'].includes(status.status) : false;

  useEffect(() => {
    if (!pendingActionId) return;
    if (status && !['pending', 'started'].includes(status.status)) {
      setPendingActionId(null);
    }
  }, [pendingActionId, status]);

  const label = action.label || action.tool || 'Action';
  const styleKey = action.style || 'secondary';
  const classes = STYLE_CLASSES[styleKey] || STYLE_CLASSES.secondary;
  const sizeClass = SIZE_CLASSES[size] || SIZE_CLASSES.md;

  const handleClick = async () => {
    if (!onAction || isLoading) return;
    if (action.confirm && typeof window !== 'undefined') {
      const confirmed = window.confirm(action.confirm);
      if (!confirmed) return;
    }
    try {
      const actionId = await Promise.resolve(onAction(action, contextData));
      if (actionId) setPendingActionId(actionId);
    } catch (err) {
      // Errors are surfaced via actionStatusMap updates
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={isLoading}
      className={`inline-flex items-center gap-2 rounded-lg border font-semibold uppercase tracking-wide transition ${classes} ${sizeClass} ${isLoading ? 'opacity-60 cursor-wait' : ''}`}
    >
      {action.icon && <span className="text-sm">{action.icon}</span>}
      <span>{isLoading ? 'Working…' : label}</span>
    </button>
  );
};

const ArtifactActionsBar = ({ actions, artifactPayload, contextData, onAction, actionStatusMap, dense = false, size = null, className = '' }) => {
  const safeActions = useMemo(() => normalizeActions(actions), [actions]);
  if (!safeActions.length) return null;

  const resolvedContext = contextData || artifactPayload || {};
  const resolvedSize = size || (dense ? 'sm' : 'md');
  const gapClass = dense ? 'gap-1.5' : 'gap-2';
  const marginClass = dense ? '' : 'mt-3';

  return (
    <div className={`flex flex-wrap items-center ${gapClass} ${marginClass} ${className}`}>
      {safeActions.map((action, idx) => (
        <ActionButton
          key={`${action.tool || 'action'}-${idx}`}
          action={action}
          contextData={resolvedContext}
          onAction={onAction}
          actionStatusMap={actionStatusMap}
          size={resolvedSize}
        />
      ))}
    </div>
  );
};

export default ArtifactActionsBar;
