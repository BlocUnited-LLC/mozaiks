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
  const progress = !connectionPhase && chatStatus.phase === 'working' ? chatStatus.progress : null;
  const detail = connectionPhase ? '' : String(progress?.label || chatStatus.detail || '').trim();
  const showCount = progress && Number.isSafeInteger(progress.completed) && Number.isSafeInteger(progress.total)
    && progress.completed >= 0 && progress.total > 0 && progress.total >= progress.completed;
  const totalTokens = chatStatus.totalTokens;
  const showTokens = Number.isInteger(totalTokens) && totalTokens >= 0;

  return (
    <div
      className="flex min-h-8 flex-shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-[rgba(var(--color-primary-light-rgb),0.16)] bg-[var(--chat-input-bg)] px-3 py-1.5 text-[11px] sm:px-4 sm:text-xs"
      role="group"
      aria-label="Chat status"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2" role="status" aria-live="polite" aria-atomic="true">
        <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full" style={{ backgroundColor: phase.color }} aria-hidden="true" />
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-medium text-[var(--color-text-primary)]">{phase.label}</span>
          {detail && <span className="max-w-full break-words text-[var(--color-text-secondary)]">{detail}</span>}
          {showCount && <span className="max-w-full break-words tabular-nums text-[var(--color-text-secondary)]">
            {progress.completed.toLocaleString()} of {progress.total.toLocaleString()}{progress.unit ? ` ${progress.unit}` : ''}
          </span>}
        </div>
      </div>
      {showTokens && (
        <span className="flex-shrink-0 whitespace-nowrap text-[var(--color-text-secondary)]" aria-label={`Run tokens: ${totalTokens.toLocaleString()}`}>
          Run tokens <span className="tabular-nums font-medium text-[var(--color-text-primary)]">{totalTokens.toLocaleString()}</span>
        </span>
      )}
    </div>
  );
}
