import { useCallback, useState } from 'react';

import {
  clearStoredArtifactState,
  clearStoredChatCacheSeed,
  setStoredActiveChatId,
} from '../session/chatSessionStorage';

export function useChatSessionHistory({
  api,
  currentAppId,
  currentUserId,
  conversationMode,
  workflowSessions,
  generalMessagesCacheRef,
  setGeneralChatSessions,
  setWorkflowSessions,
  setActiveGeneralChatId,
  setGeneralChatSummary,
  setMessagesWithLogging,
  setCurrentArtifactMessages,
}) {
  const [generalSessionsLoading, setGeneralSessionsLoading] = useState(false);
  const [workflowSessionsLoading, setWorkflowSessionsLoading] = useState(false);

  const refreshGeneralSessions = useCallback(async () => {
    if (!api || !currentAppId || !currentUserId) {
      return [];
    }
    setGeneralSessionsLoading(true);
    try {
      const result = await api.listGeneralChats(currentAppId, currentUserId, 50);
      const sessions = Array.isArray(result?.sessions) ? result.sessions : [];
      setGeneralChatSessions(sessions);
      return sessions;
    } catch (err) {
      console.error('Failed to list general chats:', err);
      return [];
    } finally {
      setGeneralSessionsLoading(false);
    }
  }, [api, currentAppId, currentUserId, setGeneralChatSessions]);

  const refreshWorkflowSessions = useCallback(async () => {
    if (!api || typeof api.get !== 'function' || !currentAppId || !currentUserId) {
      return [];
    }
    setWorkflowSessionsLoading(true);
    try {
      const result = await api.get(`/api/sessions/list/${currentAppId}/${currentUserId}`);
      const sessions = Array.isArray(result?.sessions) ? result.sessions : [];
      setWorkflowSessions(sessions);
      return sessions;
    } catch (err) {
      console.error('Failed to list workflow sessions:', err);
      setWorkflowSessions([]);
      return [];
    } finally {
      setWorkflowSessionsLoading(false);
    }
  }, [api, currentAppId, currentUserId, setWorkflowSessions]);

  const handleRefreshGeneralSessions = useCallback(() => {
    refreshGeneralSessions();
  }, [refreshGeneralSessions]);

  const handleClearGeneralSessions = useCallback(async () => {
    if (!api || typeof api.clearGeneralChats !== 'function' || !currentAppId || !currentUserId) {
      return;
    }

    const confirmed = typeof window === 'undefined'
      ? true
      : window.confirm('Clear all Ask conversations for this app and user?');
    if (!confirmed) {
      return;
    }

    setGeneralSessionsLoading(true);
    try {
      await api.clearGeneralChats(currentAppId, currentUserId, { status: 'all' });
      setGeneralChatSessions([]);
      setActiveGeneralChatId(null);
      setGeneralChatSummary(null);
      generalMessagesCacheRef.current = [];
      if (conversationMode === 'ask') {
        setMessagesWithLogging([]);
      }
      console.log('🧹 [ASK] Cleared general chat sessions');
    } catch (err) {
      console.error('Failed to clear general chat sessions:', err);
    } finally {
      setGeneralSessionsLoading(false);
    }
  }, [
    api,
    currentAppId,
    currentUserId,
    conversationMode,
    generalMessagesCacheRef,
    setActiveGeneralChatId,
    setGeneralChatSummary,
    setGeneralChatSessions,
    setMessagesWithLogging,
  ]);

  const handleRefreshWorkflowSessions = useCallback(() => {
    refreshWorkflowSessions();
  }, [refreshWorkflowSessions]);

  const handleClearWorkflowSessions = useCallback(async () => {
    if (!api || typeof api.clearWorkflowSessions !== 'function' || !currentAppId || !currentUserId) {
      return;
    }

    const confirmed = typeof window === 'undefined'
      ? true
      : window.confirm('Clear all workflow runs for this app and user?');
    if (!confirmed) {
      return;
    }

    setWorkflowSessionsLoading(true);
    try {
      const existing = Array.isArray(workflowSessions) ? workflowSessions : [];
      await api.clearWorkflowSessions(currentAppId, currentUserId, { status: 'all' });
      for (const session of existing) {
        const chatId = session?.chat_id;
        if (!chatId) continue;
        clearStoredArtifactState(chatId);
        clearStoredChatCacheSeed(chatId);
      }
      setWorkflowSessions([]);
      setStoredActiveChatId(null);
      if (conversationMode === 'workflow') {
        setMessagesWithLogging([]);
        setCurrentArtifactMessages([]);
      }
      console.log('🧹 [WORKFLOW] Cleared workflow sessions');
    } catch (err) {
      console.error('Failed to clear workflow sessions:', err);
    } finally {
      setWorkflowSessionsLoading(false);
    }
  }, [
    api,
    currentAppId,
    currentUserId,
    workflowSessions,
    conversationMode,
    setMessagesWithLogging,
    setWorkflowSessions,
    setCurrentArtifactMessages,
  ]);

  return {
    generalSessionsLoading,
    workflowSessionsLoading,
    refreshGeneralSessions,
    refreshWorkflowSessions,
    handleRefreshGeneralSessions,
    handleClearGeneralSessions,
    handleRefreshWorkflowSessions,
    handleClearWorkflowSessions,
  };
}