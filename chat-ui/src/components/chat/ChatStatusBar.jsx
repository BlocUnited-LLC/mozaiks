import React from 'react';

const PHASES = {
  idle: { label: 'Ready', color: 'var(--color-text-secondary)' },
  working: { label: 'Working', color: 'var(--color-primary-light)' },
  waiting: { label: 'Waiting for you', color: 'var(--color-warning)' },
  complete: { label: 'Complete', color: 'var(--color-success)' },
  error: { label: 'Needs attention', color: 'var(--color-error)' },
};

export default function ChatStatusBar({ chatStatus, connectionStatus }) {
  if (!chatStatus) return null;

  const connectionPhase = connectionStatus === 'error' || connectionStatus === 'disconnected'
    ? { label: 'Disconnected', color: 'var(--color-error)' }
    : connectionStatus === 'connecting' || connectionStatus === 'reconnecting'
      ? { label: 'Connecting', color: 'var(--color-warning)' }
      : null;
  const phase = connectionPhase || PHASES[chatStatus.phase] || PHASES.idle;
  const detail = connectionPhase ? '' : String(chatStatus.detail || '').trim();
  const totalTokens = chatStatus.totalTokens;
  const showTokens = Number.isInteger(totalTokens) && totalTokens >= 0;

  return (
    <div
      className="flex min-h-8 flex-shrink-0 items-center justify-between gap-3 border-t border-[rgba(var(--color-primary-light-rgb),0.16)] bg-[var(--chat-input-bg)] px-3 py-1.5 text-[11px] sm:px-4 sm:text-xs"
      role="group"
      aria-label="Chat status"
    >
      <div className="flex min-w-0 items-center gap-2" role="status" aria-live="polite" aria-atomic="true">
        <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ backgroundColor: phase.color }} aria-hidden="true" />
        <span className="flex-shrink-0 font-medium text-[var(--color-text-primary)]">{phase.label}</span>
        {detail && <span className="truncate text-[var(--color-text-secondary)]">{detail}</span>}
      </div>
      {showTokens && (
        <span className="flex-shrink-0 whitespace-nowrap text-[var(--color-text-secondary)]" aria-label={`Run tokens: ${totalTokens.toLocaleString()}`}>
          Run tokens <span className="tabular-nums font-medium text-[var(--color-text-primary)]">{totalTokens.toLocaleString()}</span>
        </span>
      )}
    </div>
  );
}
