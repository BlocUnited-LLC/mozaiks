/**
 * useWorkflowStart — unified hook for starting any workflow from the shell.
 *
 * All workflow starts converge here regardless of trigger source:
 *   - transition resolution → trigger_source: "transition"
 *   - action button      → trigger_source: "action", action_id: "..."
 *   - refinement control → trigger_source: "refinement", change_class: "patch|design|feature|core"
 *   - direct chat        → trigger_source: "chat" (default)
 *
 * Behavior:
 *   - trigger_source "chat": navigates to /chat?workflow=X&context=Y (existing flow)
 *   - all other sources: POST /api/workflows/trigger → get chat_id → navigate to /chat/{chat_id}
 *     This ensures trigger metadata is stored server-side and context is validated.
 *
 * Usage:
 *   const { startWorkflow, starting, error } = useWorkflowStart()
 *
 *   startWorkflow('AppGenerator', { app_type: 'new' }, { trigger_source: 'transition' })
 *   startWorkflow('PatchWorkflow', { artifact_version_id: 'v3' }, { trigger_source: 'refinement', change_class: 'patch' })
 *   startWorkflow('ContactAnalyzer', { contact_id }, { trigger_source: 'action', action_id: 'analyze_contact' })
 */

import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';

const CHAT_TRIGGER_SOURCE = 'chat';

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
        change_class = null,
        artifact_version_id = null,
        artifact_kind = null,
        raw_user_request = null,
        app_id = null,
        user_id = null,
      } = options;
      const resolvedAppId = resolveWorkflowAppId(config, user, app_id);
      const resolvedUserId = resolveWorkflowUserId(user, user_id);

      setError(null);

      // Chat trigger — use existing URL-based flow (ChatPage owns the session start)
      if (trigger_source === CHAT_TRIGGER_SOURCE) {
        const params = new URLSearchParams({ workflow: workflowId });
        if (contextVariables && Object.keys(contextVariables).length > 0) {
          params.set('context', JSON.stringify(contextVariables));
        }
        navigate(`/chat?${params.toString()}`);
        return;
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
          ...(change_class ? { change_class } : {}),
          ...(artifact_version_id ? { artifact_version_id } : {}),
          ...(artifact_kind ? { artifact_kind } : {}),
          ...(raw_user_request ? { raw_user_request } : {}),
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

        const { chat_id, workflow_id } = await res.json();
        // Navigate to the chat for this session
        navigate(`/chat?workflow=${encodeURIComponent(workflow_id)}&chat_id=${encodeURIComponent(chat_id)}`);
      } catch (err) {
        setError(err.message);
        console.error('[useWorkflowStart] trigger failed:', err);
      } finally {
        setStarting(false);
      }
    },
    [config, navigate, user]
  );

  return { startWorkflow, starting, error };
}
