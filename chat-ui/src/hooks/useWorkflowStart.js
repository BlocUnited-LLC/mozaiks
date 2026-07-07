/**
 * useWorkflowStart — unified hook for starting any workflow from the shell.
 *
 * All workflow starts converge here regardless of trigger source:
 *   - transition resolution → trigger_source: "transition"
 *   - persistent page action → trigger_source: "page"
 *   - action button      → trigger_source: "action", action_id: "..."
 *   - routed non-chat trigger → trigger_source: "refinement" | "route" | ..., trigger_payload: {...}
 *   - direct chat        → trigger_source: "chat" (default)
 *
 * Behavior:
 *   - trigger_source "chat": navigates to /chat?workflow=X&context=Y (existing flow)
 *   - all other sources: POST /api/workflows/trigger
 *     - if the response contains chat_id + workflow_id, navigate to that chat
 *     - otherwise return the backend result to the caller without navigation
 *     This ensures trigger metadata is stored server-side and context is validated.
 *
 * Usage:
 *   const { startWorkflow, starting, error } = useWorkflowStart()
 *
 *   startWorkflow('AppGenerator', { app_type: 'new' }, { trigger_source: 'transition' })
 *   startWorkflow(null, {}, { trigger_source: 'refinement', trigger_payload: { change_class: 'patch' } })
 *   startWorkflow('ContactAnalyzer', { contact_id }, { trigger_source: 'action', action_id: 'analyze_contact' })
 */

import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';

const CHAT_TRIGGER_SOURCE = 'chat';

const buildWorkflowChatUrl = (workflowId, chatId = null, contextVariables = null) => {
  const params = new URLSearchParams({
    mode: 'workflow',
    workflow: String(workflowId || ''),
  });
  if (chatId) {
    params.set('chat_id', String(chatId));
  }
  if (contextVariables && Object.keys(contextVariables).length > 0) {
    params.set('context', JSON.stringify(contextVariables));
  }
  return `/chat?${params.toString()}`;
};

const resolveWorkflowAppId = (config, user, overrideAppId) => {
  return (
    overrideAppId ||
    user?.app_id ||
    config?.chat?.defaultAppId ||
    config?.appId ||
    config?.app_id ||
    'default'
  );
};

const resolveWorkflowUserId = (user, overrideUserId) => {
  return (
    overrideUserId ||
    user?.id ||
    user?.user_id ||
    user?.email ||
    null
  );
};

export function useWorkflowStart() {
  const navigate = useNavigate();
  const { user, config } = useChatUI();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(null);

  const startWorkflow = useCallback(
    async (workflowId, contextVariables = {}, options = {}) => {
      const {
        trigger_source = CHAT_TRIGGER_SOURCE,
        action_id = null,
        trigger_payload = null,
        journey_id = null,
        app_id = null,
        user_id = null,
      } = options;
      const resolvedAppId = resolveWorkflowAppId(config, user, app_id);
      const resolvedUserId = resolveWorkflowUserId(user, user_id);

      setError(null);

      // Chat trigger — always start a fresh session (no stored chat_id resume)
      if (trigger_source === CHAT_TRIGGER_SOURCE) {
        const url = buildWorkflowChatUrl(workflowId, null, contextVariables);
        navigate(url + '&new=1');
        return { execution_mode: 'chat_navigation', workflow_id };
      }

      // All other trigger sources — use the unified backend endpoint
      // Context is validated server-side against context_variables.yaml
      setStarting(true);
      try {
        const body = {
          trigger_source,
          context_variables: contextVariables,
          app_id: resolvedAppId,
          user_id: resolvedUserId,
          // workflow_id is optional for refinement triggers — backend router resolves it
          ...(workflowId ? { workflow_id: workflowId } : {}),
          ...(action_id ? { action_id } : {}),
          ...(journey_id ? { journey_id } : {}),
          ...(trigger_payload && Object.keys(trigger_payload).length > 0 ? { trigger_payload } : {}),
        };

        const res = await fetch('/api/workflows/trigger', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(err.detail || 'Failed to trigger workflow');
        }

        const payload = await res.json();
        const { chat_id, workflow_id } = payload || {};
        if (chat_id && workflow_id) {
          navigate(buildWorkflowChatUrl(workflow_id, chat_id));
        }
        return payload;
      } catch (err) {
        setError(err.message);
        console.error('[useWorkflowStart] trigger failed:', err);
        return null;
      } finally {
        setStarting(false);
      }
    },
    [config, navigate, user]
  );

  return { startWorkflow, starting, error };
}
