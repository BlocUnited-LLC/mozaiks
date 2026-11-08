const BOUNDARIES = new Set(['run_start', 'run_complete', 'error', 'awaiting_reply', 'resume_boundary', 'tool_call']);
const PAUSED_BOUNDARIES = new Set(['run_complete', 'error', 'awaiting_reply']);
const ACTIVE_BOUNDARIES = new Set(['run_start', 'tool_call']);

const scopeKeyFor = ({ mode, chatId, workflowName }) => (
  mode === 'workflow' && chatId && workflowName ? `${mode}:${chatId}:${workflowName}` : null
);

export const emptyChatStatusProgress = () => ({ scopeKey: null, sequence: null, progress: null, blocked: false });

// Clearing a visible stage must not allow an older transport event to revive it.
export function clearChatStatusProgress(current, scope) {
  const scopeKey = scopeKeyFor(scope);
  if (current.scopeKey !== scopeKey) return { ...emptyChatStatusProgress(), scopeKey };
  return current.progress ? { ...current, progress: null } : current;
}

export function beginChatStatusProgressTurn(current, scope) {
  const cleared = clearChatStatusProgress(current, scope);
  return cleared.blocked ? { ...cleared, blocked: false } : cleared;
}

export function applyChatProgressEvent(current, scope, envelope) {
  const scopeKey = scopeKeyFor(scope);
  if (!scopeKey) return current;
  const event = { ...envelope?.data, ...envelope };
  const type = String(event.type || '').replace(/^chat\./, '');
  if (type !== 'tool_progress' && !BOUNDARIES.has(type)) return current;
  if (event.replay || String(event.chat_id || '') !== String(scope.chatId)) return current;
  if (event.workflow_name && event.workflow_name !== scope.workflowName) return current;

  const active = current.scopeKey === scopeKey ? current : { ...emptyChatStatusProgress(), scopeKey };
  const sequence = Number.isSafeInteger(event.sequence) && event.sequence >= 0 ? event.sequence : null;
  if (sequence !== null && active.sequence !== null && sequence <= active.sequence) return current;
  if (BOUNDARIES.has(type)) {
    const blocked = PAUSED_BOUNDARIES.has(type) || (!ACTIVE_BOUNDARIES.has(type) && active.blocked);
    return { ...active, sequence: sequence ?? active.sequence, progress: null, blocked };
  }

  // Only declared, sequenced workflow stages belong in the shared status strip.
  if (active.blocked || sequence === null || event.workflow_name !== scope.workflowName
      || typeof event.stage_id !== 'string' || !event.stage_id.trim()
      || typeof event.stage_label !== 'string' || !event.stage_label.trim()) return current;
  const completed = event.completed ?? null;
  const total = event.total ?? null;
  if (!(completed === null && total === null)
      && !(Number.isSafeInteger(completed) && Number.isSafeInteger(total)
        && completed >= 0 && total > 0 && total >= completed)) return current;

  return {
    scopeKey,
    sequence,
    blocked: false,
    progress: {
      stageId: event.stage_id,
      label: event.stage_label.trim(),
      completed,
      total,
      unit: typeof event.unit === 'string' ? event.unit.trim() : '',
    },
  };
}

export function getChatStatusProgress(current, scope) {
  return current.scopeKey === scopeKeyFor(scope) ? current.progress : null;
}
