import { useCallback, useEffect, useRef, useState } from 'react';
import { useWorkflowStart } from './useWorkflowStart';

// Persisted ChatSession status, not an approval decision or an individual tool error.
export const isFailedWorkflowSession = (status) => status === 2;

export function useFailedWorkflowRetry({ appId, userId, chatId, workflowName, surface, mode, blocked }) {
  const scope = JSON.stringify([appId, userId, chatId, workflowName, surface, mode]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const request = useRef(null);
  const [failedScope, setFailedScope] = useState(null);
  const [attemptScope, setAttemptScope] = useState(null);
  const [launchedScope, setLaunchedScope] = useState(null);
  const { startWorkflow, starting, error } = useWorkflowStart();

  useEffect(() => () => {
    if (request.current?.scope === scope) request.current.controller.abort();
  }, [scope]);

  const observeSessionMeta = useCallback((meta) => {
    if (currentScope.current !== scope || !meta || meta.chat_id !== chatId
      || meta.app_id !== appId || meta.workflow_name !== workflowName) return;
    setFailedScope(meta.exists !== false && meta.chat_exists !== false
      && isFailedWorkflowSession(meta.status) ? scope : null);
  }, [scope, appId, chatId, workflowName]);

  const available = Boolean(surface === 'studio' && mode === 'workflow' && !blocked
    && appId && userId && chatId && workflowName && failedScope === scope);
  const retry = useCallback(async () => {
    // A ref closes the same-tick double-click window before React renders disabled.
    if (!available || currentScope.current !== scope || request.current || launchedScope === scope) return null;
    const pending = { scope, controller: new AbortController() };
    request.current = pending;
    setAttemptScope(scope);
    try {
      const result = await startWorkflow(workflowName, {}, {
        trigger_source: 'manual', app_id: appId, user_id: userId,
        source_chat_id: chatId, retry_failed: true, signal: pending.controller.signal,
      });
      if (result && !pending.controller.signal.aborted && currentScope.current === scope) {
        setLaunchedScope(scope);
      }
      return result;
    } finally {
      if (request.current === pending) request.current = null;
    }
  }, [available, launchedScope, scope, startWorkflow, workflowName, appId, userId, chatId]);

  return {
    observeSessionMeta,
    available,
    retry,
    starting: (starting && attemptScope === scope) || launchedScope === scope,
    error: attemptScope === scope ? error : null,
  };
}
