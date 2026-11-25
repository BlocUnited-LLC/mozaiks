import { useCallback, useEffect, useRef, useState } from 'react';

// Admission is asynchronous. A socket write alone cannot establish that AG2
// received a turn; use the server receipt to identify the optimistic message.
export default function useWorkflowSubmission(scope, removeRefusedMessage) {
  const activeScope = useRef(scope);
  activeScope.current = scope;
  const submitted = useRef(null);
  const [draft, setDraft] = useState(null);
  const [refused, setRefused] = useState(null);

  useEffect(() => {
    setDraft(previous => previous?.scope === scope ? previous : null);
    setRefused(previous => previous?.scope === scope ? previous : null);
    if (submitted.current?.scope !== scope) submitted.current = null;
  }, [scope]);

  const changeDraft = useCallback(text => {
    setDraft({ scope, text });
  }, [scope]);

  const remember = useCallback((targetScope, chatId, message) => {
    const entry = { scope: targetScope, chatId, message, accepted: false, rejected: false };
    submitted.current = entry;
    setRefused(null);
    return entry;
  }, []);

  const restore = useCallback(entry => {
    if (!entry || entry !== submitted.current || entry.rejected || entry.scope !== activeScope.current) return false;
    entry.rejected = true;
    const unsent = { scope: entry.scope, text: entry.message.content };
    setRefused(unsent);
    setDraft(previous => previous?.scope === entry.scope && previous.text ? previous : unsent);
    removeRefusedMessage(entry.message.id);
    return true;
  }, [removeRefusedMessage]);

  const handleAdmission = useCallback((accepted, chatId, messageId) => {
    const entry = submitted.current;
    if (!entry || entry.scope !== activeScope.current || entry.chatId !== chatId ||
        entry.message.id !== messageId || entry.accepted || entry.rejected) return false;
    if (accepted) entry.accepted = true;
    else restore(entry);
    return true;
  }, [restore]);

  return {
    text: draft?.scope === scope ? draft.text : '',
    refused: refused?.scope === scope ? refused : null,
    changeDraft, submitted, remember, restore, handleAdmission,
  };
}
