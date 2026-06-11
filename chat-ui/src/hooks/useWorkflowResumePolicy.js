import { useCallback } from 'react';

import resolveWorkflow from '../utils/resolveWorkflow';
import {
  getStoredActiveChatId,
  getStoredActiveWorkflowName,
} from '../session/chatSessionStorage';

export function useWorkflowResumePolicy({
  configuredResumePolicy,
  resolveKnownWorkflowName,
  activeWorkflowName,
  currentWorkflowName,
  configuredEntryWorkflow,
  api,
  currentAppId,
  currentUserId,
  workflowSessions,
  conversationMode,
  activeChatId,
  currentChatId,
}) {
  const resolveResumePolicyOrder = useCallback(() => {
    const policy = String(configuredResumePolicy || 'last_active_then_oldest_then_entry_point').toLowerCase().trim();
    const policyMap = {
      last_active_then_oldest_then_entry_point: ['last_active', 'oldest', 'entry_point'],
      last_active_then_recent_then_entry_point: ['last_active', 'recent', 'entry_point'],
      recent_then_entry_point: ['recent', 'entry_point'],
      oldest_then_entry_point: ['oldest', 'entry_point'],
      entry_point_only: ['entry_point'],
    };
    return policyMap[policy] || ['last_active', 'oldest', 'entry_point'];
  }, [configuredResumePolicy]);

  const describeApiError = useCallback((error) => ({
    status: error?.status || null,
    message: error?.message || String(error),
    body: error?.body || null,
  }), []);

  const resolveWorkflowSessionByStrategy = useCallback(async (strategy) => {
    const normalized = String(strategy || '').toLowerCase();
    const fallbackWorkflowName =
      resolveKnownWorkflowName(activeWorkflowName)
      || resolveKnownWorkflowName(currentWorkflowName)
      || resolveKnownWorkflowName(getStoredActiveWorkflowName())
      || resolveKnownWorkflowName(configuredEntryWorkflow);

    if (normalized === 'last_active') {
      const shouldRestoreStoredWorkflowChat = !(conversationMode === 'ask');
      if (!shouldRestoreStoredWorkflowChat) {
        return null;
      }
      const lastActiveChatId = activeChatId || currentChatId || getStoredActiveChatId();
      if (!lastActiveChatId) {
        return null;
      }
      if (Array.isArray(workflowSessions) && workflowSessions.length > 0) {
        const isActiveSession = workflowSessions.some((session) => session?.chat_id === lastActiveChatId);
        if (!isActiveSession) {
          return null;
        }
      } else if (api && typeof api.get === 'function' && currentAppId && currentUserId) {
        try {
          const listed = await api.get(`/api/sessions/list/${currentAppId}/${currentUserId}`);
          const sessions = Array.isArray(listed?.sessions) ? listed.sessions : [];
          const isActiveSession = sessions.some((session) => session?.chat_id === lastActiveChatId);
          if (!isActiveSession) {
            return null;
          }
        } catch (err) {
          console.warn('Failed to validate last_active workflow session:', describeApiError(err));
          return null;
        }
      }
      return {
        strategy: 'last_active',
        chat_id: lastActiveChatId,
        workflow_name: fallbackWorkflowName || resolveWorkflow() || null,
      };
    }

    if (!api || typeof api.get !== 'function' || !currentAppId || !currentUserId) {
      return null;
    }

    if (normalized === 'recent') {
      try {
        const recent = await api.get(`/api/sessions/recent/${currentAppId}/${currentUserId}`);
        if (recent?.found && recent?.chat_id) {
          return {
            strategy: 'recent',
            chat_id: recent.chat_id,
            workflow_name: recent.workflow_name || fallbackWorkflowName || null,
          };
        }
      } catch (err) {
        console.warn('Failed to resolve recent workflow session:', describeApiError(err));
      }
      return null;
    }

    if (normalized === 'oldest') {
      try {
        const oldest = await api.get(`/api/sessions/oldest/${currentAppId}/${currentUserId}`);
        if (oldest?.found && oldest?.chat_id) {
          return {
            strategy: 'oldest',
            chat_id: oldest.chat_id,
            workflow_name: oldest.workflow_name || fallbackWorkflowName || null,
          };
        }
      } catch (err) {
        console.warn('Failed to resolve oldest workflow session:', describeApiError(err));
      }
      return null;
    }

    return null;
  }, [
    activeChatId,
    currentChatId,
    activeWorkflowName,
    currentWorkflowName,
    describeApiError,
    resolveKnownWorkflowName,
    configuredEntryWorkflow,
    api,
    currentAppId,
    currentUserId,
    workflowSessions,
    conversationMode,
  ]);

  return {
    resolveResumePolicyOrder,
    describeApiError,
    resolveWorkflowSessionByStrategy,
  };
}