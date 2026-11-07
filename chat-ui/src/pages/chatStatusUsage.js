// A usage delta is one measured model call. Keep the visible run count scoped to
// the active chat and event IDs so reconnect/replay cannot count a call twice.
export const emptyChatStatusUsage = () => ({
  scopeKey: null,
  totalTokens: null,
  seenEventIds: new Set(),
});

const scopeKeyFor = ({ mode, chatId }) => mode && chatId ? `${mode}:${chatId}` : null;

export function resetChatStatusRun(current, scope) {
  const scopeKey = scopeKeyFor(scope);
  return {
    scopeKey,
    totalTokens: null,
    seenEventIds: current.scopeKey === scopeKey ? current.seenEventIds : new Set(),
  };
}

export function applyChatUsageDelta(current, scope, event) {
  const scopeKey = scopeKeyFor(scope);
  if (!scopeKey || String(event?.chat_id || '') !== String(scope.chatId)) return current;
  const eventId = String(event?.event_id || '').trim();
  const tokens = Number(event?.total_tokens);
  if (!eventId || !Number.isSafeInteger(tokens) || tokens <= 0) return current;

  const active = current.scopeKey === scopeKey ? current : emptyChatStatusUsage();
  if (active.seenEventIds.has(eventId)) return current;
  const seenEventIds = new Set(active.seenEventIds);
  seenEventIds.add(eventId);
  return {
    scopeKey,
    totalTokens: (active.totalTokens ?? 0) + tokens,
    seenEventIds,
  };
}
