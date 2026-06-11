const formatHistoryTimestamp = (timestamp) => {
  if (!timestamp) return 'Just now';
  try {
    const parsed = new Date(timestamp);
    if (Number.isNaN(parsed.getTime())) return 'Just now';
    return parsed.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch {
    return 'Just now';
  }
};

export const AskHistorySidebar = ({
  sessions = [],
  activeChatId,
  loading,
  onSelectChat,
  onStartNewChat,
  onRefresh,
  onClear,
}) => {
  const hasSessions = Array.isArray(sessions) && sessions.length > 0;
  return (
    <aside className="hidden lg:flex flex-col w-64 xl:w-72 2xl:w-80 shrink-0 self-stretch min-h-0 h-full rounded-2xl border border-[rgba(var(--color-primary-light-rgb),0.25)] bg-[rgba(2,6,23,0.72)] backdrop-blur-xl shadow-[0_20px_60px_rgba(2,6,23,0.6)] px-4 py-4 text-sm text-slate-100">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-[11px] tracking-[0.2em] uppercase text-[rgba(148,163,184,0.9)]">Ask</p>
          <h2 className="text-lg font-semibold">Recent Conversations</h2>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="px-2 py-1 rounded-lg border border-[rgba(248,113,113,0.45)] text-[11px] tracking-wide text-[rgba(254,202,202,0.92)] hover:text-white hover:border-[rgba(248,113,113,0.8)] transition disabled:opacity-60"
            onClick={onClear}
            disabled={loading}
          >
            Clear
          </button>
          <button
            type="button"
            className="px-2 py-1 rounded-lg border border-[rgba(var(--color-primary-light-rgb),0.4)] text-[11px] tracking-wide text-[rgba(148,163,184,0.9)] hover:text-white hover:border-[rgba(var(--color-primary-light-rgb),0.8)] transition"
            onClick={onRefresh}
            disabled={loading}
          >
            {loading ? '…' : 'Refresh'}
          </button>
        </div>
      </div>
      <div className="mt-3 flex flex-col gap-2">
        <button
          type="button"
          onClick={onStartNewChat}
          className="w-full inline-flex items-center justify-center gap-2 rounded-xl border border-[rgba(var(--color-secondary-rgb),0.35)] bg-[rgba(var(--color-secondary-rgb),0.12)] py-2 text-sm font-semibold text-white hover:border-[rgba(var(--color-secondary-rgb),0.55)] hover:bg-[rgba(var(--color-secondary-rgb),0.18)] transition"
        >
          <span className="text-base" aria-hidden="true">＋</span>
          New Chat
        </button>
        <p className="text-[12px] text-[rgba(148,163,184,0.9)]">{hasSessions ? `${sessions.length} saved session${sessions.length === 1 ? '' : 's'}` : 'No saved conversations yet.'}</p>
      </div>
      <div className="mt-3 flex-1 overflow-y-auto my-scroll1 pr-1 space-y-2">
        {hasSessions ? (
          sessions.map((session) => {
            const chatId = session?.chat_id;
            if (!chatId) return null;
            const isActive = chatId === activeChatId;
            const label = session?.label || `Chat ${chatId.slice(-4)}`;
            const summary = session?.summary || session?.last_message_preview || 'Tap to resume conversation.';
            const timestamp = formatHistoryTimestamp(session?.last_updated_at || session?.updated_at);
            return (
              <button
                key={chatId}
                type="button"
                onClick={() => onSelectChat(chatId)}
                className={`w-full text-left rounded-2xl border px-3 py-2 transition focus:outline-none focus:ring-2 focus:ring-[rgba(var(--color-primary-light-rgb),0.6)] ${isActive
                  ? 'border-[rgba(var(--color-primary-light-rgb),0.7)] bg-[rgba(var(--color-primary-rgb),0.15)] shadow-[0_10px_35px_rgba(6,182,212,0.15)]'
                  : 'border-[rgba(148,163,184,0.2)] bg-[rgba(15,23,42,0.65)] hover:border-[rgba(148,163,184,0.4)] hover:bg-[rgba(15,23,42,0.8)]'}`}
              >
                <div className="flex items-center justify-between text-[11px] text-[rgba(148,163,184,0.95)]">
                  <span className="font-semibold text-[rgba(226,232,240,0.9)] text-sm">{label}</span>
                  <span>{timestamp}</span>
                </div>
                <p className="mt-1 text-[13px] leading-relaxed text-[rgba(226,232,240,0.8)] max-h-[3.6rem] overflow-hidden">{summary}</p>
              </button>
            );
          })
        ) : (
          <div className="rounded-2xl border border-dashed border-[rgba(148,163,184,0.4)] px-3 py-6 text-center text-[13px] text-[rgba(148,163,184,0.9)]">
            Start a conversation to see it appear here.
          </div>
        )}
      </div>
    </aside>
  );
};

export const MobileAskHistoryDrawer = ({
  mode = 'ask',
  open,
  sessions = [],
  activeChatId,
  loading,
  onSelectChat,
  onSelectWorkflow,
  onStartNewChat,
  onStartEntryWorkflow,
  onRefresh,
  onClear,
  onClose,
}) => {
  if (!open) {
    return null;
  }

  const isWorkflowMode = mode === 'workflow';
  const hasSessions = Array.isArray(sessions) && sessions.length > 0;
  const title = isWorkflowMode ? 'Recent Workflows' : 'Recent Chats';
  const modeLabel = isWorkflowMode ? 'Workflow' : 'Ask';
  const emptyText = isWorkflowMode
    ? 'Start a workflow run to see it appear here.'
    : 'Start a conversation to see it appear here.';
  const countText = hasSessions
    ? `${sessions.length} saved ${isWorkflowMode ? `workflow run${sessions.length === 1 ? '' : 's'}` : `conversation${sessions.length === 1 ? '' : 's'}`}`
    : (isWorkflowMode ? 'No saved workflow runs yet.' : 'No saved conversations yet.');
  const launchButtonLabel = isWorkflowMode ? 'Open Workflow' : 'New Chat';

  return (
    <div className="fixed inset-0 z-[70] lg:hidden pointer-events-none">
      <button
        type="button"
        aria-label={`Close ${modeLabel} history`}
        className="absolute inset-0 bg-black/55 backdrop-blur-sm pointer-events-auto"
        onClick={onClose}
      ></button>
      <div className="absolute inset-x-0 bottom-0 pointer-events-auto px-2 sm:px-3 pb-[calc(env(safe-area-inset-bottom,0px)+0.35rem)]">
        <div className="mx-auto w-full max-w-3xl rounded-t-3xl border border-[rgba(var(--color-primary-light-rgb),0.35)] bg-[rgba(5,10,24,0.96)] backdrop-blur-2xl shadow-[0_20px_60px_rgba(2,6,23,0.85)] flex flex-col overflow-hidden max-h-[min(78dvh,720px)]">
          <div className="flex justify-center pt-2 pb-1">
            <div className="h-1 w-10 rounded-full bg-[rgba(148,163,184,0.45)]" />
          </div>

          <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[rgba(var(--color-primary-light-rgb),0.25)]">
            <div>
              <p className="text-[10px] tracking-[0.3em] uppercase text-[rgba(148,163,184,0.8)]">{modeLabel}</p>
              <h2 className="text-base font-semibold text-white">{title}</h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[rgba(var(--color-primary-light-rgb),0.35)] text-[11px] tracking-[0.15em] uppercase text-[rgba(226,232,240,0.95)] hover:border-[rgba(var(--color-primary-light-rgb),0.75)] hover:text-white transition"
            >
              <span aria-hidden="true">←</span>
              Back To Chat
            </button>
          </div>

          <div className="px-4 py-3 border-b border-[rgba(148,163,184,0.35)]">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  if (isWorkflowMode) {
                    onStartEntryWorkflow?.();
                  } else {
                    onStartNewChat?.();
                  }
                  onClose?.();
                }}
                className="flex-1 inline-flex items-center justify-center gap-2 rounded-xl border border-[rgba(var(--color-secondary-rgb),0.35)] bg-[rgba(var(--color-secondary-rgb),0.15)] py-2 text-sm font-semibold text-white"
              >
                <span className="text-lg" aria-hidden="true">＋</span>
                {launchButtonLabel}
              </button>
              <button
                type="button"
                onClick={onRefresh}
                disabled={loading}
                className="px-3 py-2 rounded-xl border border-[rgba(var(--color-primary-light-rgb),0.35)] text-xs text-[rgba(148,163,184,0.95)] hover:border-[rgba(var(--color-primary-light-rgb),0.7)] hover:text-white transition disabled:opacity-60"
              >
                {loading ? '…' : 'Refresh'}
              </button>
              <button
                type="button"
                onClick={onClear}
                disabled={loading}
                className="px-3 py-2 rounded-xl border border-[rgba(248,113,113,0.45)] text-xs text-[rgba(254,202,202,0.95)] hover:border-[rgba(248,113,113,0.8)] hover:text-white transition disabled:opacity-60"
              >
                Clear
              </button>
            </div>
            <p className="mt-2 text-[12px] text-[rgba(148,163,184,0.9)]">
              {countText}
            </p>
          </div>

          <div className="flex-1 overflow-y-auto my-scroll1 px-3 py-3 space-y-2">
            {hasSessions ? (
              sessions.map((session) => {
                const chatId = session?.chat_id;
                if (!chatId) return null;
                const isActive = chatId === activeChatId;
                const label = isWorkflowMode
                  ? (session?.workflow_name || `Workflow ${chatId.slice(-4)}`)
                  : (session?.label || `Chat ${chatId.slice(-4)}`);
                const summary = isWorkflowMode
                  ? (session?.last_artifact?.component_name
                      ? `Last artifact: ${session.last_artifact.component_name}`
                      : 'Tap to resume workflow run.')
                  : (session?.summary || session?.last_message_preview || 'Tap to resume conversation.');
                const timestamp = formatHistoryTimestamp(session?.last_updated_at || session?.updated_at);
                return (
                  <button
                    key={chatId}
                    type="button"
                    onClick={() => {
                      if (isWorkflowMode) {
                        onSelectWorkflow?.(chatId, session?.workflow_name || null);
                      } else {
                        onSelectChat?.(chatId);
                      }
                      onClose?.();
                    }}
                    className={`w-full text-left rounded-2xl border px-3 py-2 transition focus:outline-none focus:ring-2 focus:ring-[rgba(var(--color-primary-light-rgb),0.6)] ${isActive
                      ? 'border-[rgba(var(--color-primary-light-rgb),0.7)] bg-[rgba(var(--color-primary-rgb),0.15)] shadow-[0_10px_35px_rgba(6,182,212,0.15)]'
                      : 'border-[rgba(148,163,184,0.2)] bg-[rgba(15,23,42,0.65)] hover:border-[rgba(148,163,184,0.4)] hover:bg-[rgba(15,23,42,0.8)]'}`}
                  >
                    <div className="flex items-center justify-between text-[11px] text-[rgba(148,163,184,0.95)]">
                      <span className="font-semibold text-[rgba(226,232,240,0.95)] text-sm">{label}</span>
                      <span>{timestamp}</span>
                    </div>
                    <p className="mt-1 text-[13px] leading-relaxed text-[rgba(226,232,240,0.85)] max-h-[3.6rem] overflow-hidden">{summary}</p>
                  </button>
                );
              })
            ) : (
              <div className="rounded-2xl border border-dashed border-[rgba(148,163,184,0.4)] px-3 py-6 text-center text-[13px] text-[rgba(148,163,184,0.9)]">
                {emptyText}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export const WorkflowHistorySidebar = ({
  sessions = [],
  activeChatId,
  loading,
  onSelectSession,
  onRefresh,
  onClear,
  onStartEntryWorkflow,
}) => {
  const hasSessions = Array.isArray(sessions) && sessions.length > 0;
  return (
    <aside className="hidden lg:flex flex-col w-64 xl:w-72 2xl:w-80 shrink-0 self-stretch min-h-0 h-full rounded-2xl border border-[rgba(var(--color-primary-light-rgb),0.25)] bg-[rgba(2,6,23,0.72)] backdrop-blur-xl shadow-[0_20px_60px_rgba(2,6,23,0.6)] px-4 py-4 text-sm text-slate-100">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-[11px] tracking-[0.2em] uppercase text-[rgba(148,163,184,0.9)]">Workflow</p>
          <h2 className="text-lg font-semibold">Recent Workflows</h2>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="px-2 py-1 rounded-lg border border-[rgba(248,113,113,0.45)] text-[11px] tracking-wide text-[rgba(254,202,202,0.92)] hover:text-white hover:border-[rgba(248,113,113,0.8)] transition disabled:opacity-60"
            onClick={onClear}
            disabled={loading}
          >
            Clear
          </button>
          <button
            type="button"
            className="px-2 py-1 rounded-lg border border-[rgba(var(--color-primary-light-rgb),0.4)] text-[11px] tracking-wide text-[rgba(148,163,184,0.9)] hover:text-white hover:border-[rgba(var(--color-primary-light-rgb),0.8)] transition"
            onClick={onRefresh}
            disabled={loading}
          >
            {loading ? '…' : 'Refresh'}
          </button>
        </div>
      </div>
      <div className="mt-3 flex flex-col gap-2">
        <button
          type="button"
          onClick={onStartEntryWorkflow}
          className="w-full inline-flex items-center justify-center gap-2 rounded-xl border border-[rgba(var(--color-secondary-rgb),0.35)] bg-[rgba(var(--color-secondary-rgb),0.12)] py-2 text-sm font-semibold text-white hover:border-[rgba(var(--color-secondary-rgb),0.55)] hover:bg-[rgba(var(--color-secondary-rgb),0.18)] transition"
        >
          <span className="text-base" aria-hidden="true">▶</span>
          Open Workflow
        </button>
        <p className="text-[12px] text-[rgba(148,163,184,0.9)]">{hasSessions ? `${sessions.length} active run${sessions.length === 1 ? '' : 's'}` : 'No active workflows yet.'}</p>
      </div>
      <div className="mt-3 flex-1 overflow-y-auto my-scroll1 pr-1 space-y-2">
        {hasSessions ? (
          sessions.map((session) => {
            const chatId = session?.chat_id;
            if (!chatId) return null;
            const workflowName = session?.workflow_name || 'Workflow';
            const isActive = chatId === activeChatId;
            const summary = session?.last_artifact?.component_name
              ? `Last artifact: ${session.last_artifact.component_name}`
              : 'Tap to resume workflow run.';
            const timestamp = formatHistoryTimestamp(session?.last_updated_at || session?.updated_at || session?.created_at);
            return (
              <button
                key={chatId}
                type="button"
                onClick={() => onSelectSession(chatId, workflowName)}
                className={`w-full text-left rounded-2xl border px-3 py-2 transition focus:outline-none focus:ring-2 focus:ring-[rgba(var(--color-primary-light-rgb),0.6)] ${isActive
                  ? 'border-[rgba(var(--color-primary-light-rgb),0.7)] bg-[rgba(var(--color-primary-rgb),0.15)] shadow-[0_10px_35px_rgba(6,182,212,0.15)]'
                  : 'border-[rgba(148,163,184,0.2)] bg-[rgba(15,23,42,0.65)] hover:border-[rgba(148,163,184,0.4)] hover:bg-[rgba(15,23,42,0.8)]'}`}
              >
                <div className="flex items-center justify-between text-[11px] text-[rgba(148,163,184,0.95)]">
                  <span className="font-semibold text-[rgba(226,232,240,0.9)] text-sm">{workflowName}</span>
                  <span>{timestamp}</span>
                </div>
                <p className="mt-1 text-[13px] leading-relaxed text-[rgba(226,232,240,0.8)] max-h-[3.6rem] overflow-hidden">{summary}</p>
              </button>
            );
          })
        ) : (
          <div className="rounded-2xl border border-dashed border-[rgba(148,163,184,0.4)] px-3 py-6 text-center text-[13px] text-[rgba(148,163,184,0.9)]">
            Start a workflow run to see it appear here.
          </div>
        )}
      </div>
    </aside>
  );
};