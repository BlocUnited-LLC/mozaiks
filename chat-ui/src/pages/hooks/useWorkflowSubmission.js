import { useCallback, useEffect, useRef, useState } from 'react';

export function withoutWorkflowSubmission(messages, id) {
  return messages.filter(row => row.id !== id && row.id !== `thinking-${id}`);
}

// Admission is asynchronous. A socket write alone cannot establish that AG2
// received a turn; use the server receipt to identify the optimistic message.
export default function useWorkflowSubmission(scope, removeRefusedMessage) {
  const activeScope = useRef(scope);
  activeScope.current = scope;
  const submitted = useRef(null);
  const [draft, setDraft] = useState(null);
  const [refused, setRefused] = useState(null);
  const [sending, setSending] = useState(null);

  useEffect(() => {
    setDraft(previous => previous?.scope === scope ? previous : null);
    setRefused(previous => previous?.scope === scope ? previous : null);
    if (submitted.current?.scope !== scope) submitted.current = null;
  }, [scope]);

  const changeDraft = useCallback(text => {
    setDraft({ scope, text });
  }, [scope]);

  const isCurrent = useCallback(entry => Boolean(entry && entry === submitted.current &&
    (activeScope.current === entry.scope || activeScope.current === entry.originScope)), []);

  const remember = useCallback((targetScope, chatId, message) => {
    // A deferred chat can refuse its first send before its new route renders.
    const entry = { scope: targetScope, originScope: scope, chatId, message, accepted: false, rejected: false };
    submitted.current = entry;
    setSending(entry);
    setRefused(null);
    return entry;
  }, [scope]);

  const finish = useCallback(entry => {
    setSending(previous => previous === entry ? null : previous);
  }, []);

  const bindCreatedChat = useCallback((entry, chatId, chatScope) => {
    entry.chatId = chatId;
    entry.scope = chatScope;
    // A user can already be typing the next turn while the first chat is being created.
    setDraft(previous => previous?.scope === entry.originScope ? { ...previous, scope: chatScope } : previous);
  }, []);

  const restore = useCallback(entry => {
    if (!isCurrent(entry) || entry.rejected) return false;
    entry.rejected = true;
    const unsent = { scope: entry.scope, text: entry.message.content };
    setRefused(unsent);
    setDraft(previous => previous?.scope === entry.scope && previous.text ? previous : unsent);
    removeRefusedMessage(entry.message.id, entry.chatId);
    return true;
  }, [isCurrent, removeRefusedMessage]);

  const handleAdmission = useCallback((accepted, receipt) => {
    const chatId = receipt.data?.chat_id || receipt.chat_id;
    const messageId = receipt.data?.client_message_id || receipt.client_message_id;
    const entry = submitted.current;
    if (!isCurrent(entry) || entry.chatId !== chatId ||
        entry.message.id !== messageId || entry.accepted || entry.rejected) return false;
    if (accepted) entry.accepted = true;
    else restore(entry);
    return true;
  }, [isCurrent, restore]);

  return {
    text: draft?.scope === scope ? draft.text : '',
    refused: refused?.scope === scope ? refused : null,
    sending: isCurrent(sending) && !sending.rejected,
    changeDraft, remember, bindCreatedChat, finish, restore, handleAdmission, isCurrent,
  };
}
