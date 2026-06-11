import { useEffect } from 'react';

import {
  clearStoredArtifactState,
  clearStoredChatCacheSeed,
  getStoredActiveChatId,
  setStoredActiveChatId,
} from '../session/chatSessionStorage';

export function useChatStartupEffects({
  api,
  currentAppId,
  currentUserId,
  refreshGeneralSessions,
  refreshWorkflowSessions,
  conversationBootstrapRef,
  queryMode,
  navigationLoading,
  configuredStartupMode,
  setConversationMode,
  consumeNavigationQueryParams,
  askMessages,
  generalMessagesCacheRef,
  setMessagesWithLogging,
  setIsSidePanelOpen,
  setCurrentArtifactMessages,
  setLayoutMode,
  queryChatId,
  queryResume,
  resumeOldestFromWidgetRef,
  workflowArtifactSnapshotRef,
  currentChatId,
  activeChatId,
  restoreStoredArtifactForChat,
  urlWorkflowName,
  conversationMode,
  connectionStatus,
  askModeSyncedChatRef,
  wsRef,
  isSidePanelOpen,
  layoutMode,
  activeGeneralChatId,
  generalHydrationPendingRef,
  hydrateGeneralTranscript,
  workflowMessagesCacheRef,
  sanitizeVisibleWorkflowMessages,
  workflowMessages,
  messagesRef,
  workflowReplayPendingRef,
  isPrimaryChatRoute,
  isInWidgetMode,
  currentArtifactMessages,
  surfaceState,
  layoutModeForConversation,
  widgetOverlayOpen,
  setWidgetOverlayOpen,
  handleConversationModeChange,
  queryResumeHandledRef,
  currentWorkflowName,
  resumeWorkflowSession,
  resolveKnownWorkflowName,
  setIsInWidgetMode,
  setActiveChatId,
  workflowConfig,
}) {
  useEffect(() => {
    if (!api || !currentAppId || !currentUserId) {
      return;
    }
    refreshGeneralSessions();
  }, [api, currentAppId, currentUserId, refreshGeneralSessions]);

  useEffect(() => {
    if (!api || !currentAppId || !currentUserId) {
      return;
    }
    if (conversationMode !== 'workflow') {
      return;
    }
    refreshWorkflowSessions();
  }, [api, currentAppId, currentUserId, conversationMode, refreshWorkflowSessions]);

  useEffect(() => {
    if (conversationBootstrapRef.current) {
      return;
    }
    if (!queryMode && navigationLoading) {
      return;
    }
    conversationBootstrapRef.current = true;
    console.log('🧭 [BOOTSTRAP] chat_startup_mode resolved to:', configuredStartupMode);

    if (queryMode === 'ask') {
      console.log('🧭 [BOOTSTRAP] Explicit ask mode requested via URL param');
      setConversationMode('ask');
      consumeNavigationQueryParams(['mode', 'resume', 'chat_id']);
      if (askMessages && askMessages.length > 0) {
        setMessagesWithLogging(askMessages);
      } else if (generalMessagesCacheRef.current && generalMessagesCacheRef.current.length > 0) {
        setMessagesWithLogging(generalMessagesCacheRef.current);
      }
      setTimeout(() => {
        console.log('🧹 [BOOTSTRAP] Closing artifact panel for Ask mode');
        setIsSidePanelOpen(false);
        setCurrentArtifactMessages([]);
        if (setLayoutMode) setLayoutMode('full');
      }, 50);
      return;
    }

    if (queryMode === 'workflow') {
      console.log('🧭 [BOOTSTRAP] Explicit workflow mode requested via URL param');
      setConversationMode('workflow');
      consumeNavigationQueryParams(['mode', 'resume']);
      if (!queryChatId && queryResume === 'oldest') {
        resumeOldestFromWidgetRef.current = true;
      }

      const snapshot = workflowArtifactSnapshotRef.current;
      if (snapshot?.isOpen && snapshot?.messages?.length > 0) {
        console.log('🎨 [BOOTSTRAP] Restoring artifact panel from in-memory snapshot');
        setTimeout(() => {
          setIsSidePanelOpen(true);
          setCurrentArtifactMessages(snapshot.messages);
          if (snapshot.layoutMode && setLayoutMode) {
            setLayoutMode(snapshot.layoutMode);
          }
        }, 100);
      } else {
        const restoreChatId = queryChatId || currentChatId || activeChatId || getStoredActiveChatId();
        if (restoreChatId) {
          const restored = restoreStoredArtifactForChat(restoreChatId, urlWorkflowName);
          if (restored) {
            console.log('🎨 [BOOTSTRAP] Restored artifact from stored session', restoreChatId);
          }
        }
      }
      return;
    }

    const startupDefault = configuredStartupMode;
    if (startupDefault === 'ask') {
      console.log('🧭 [BOOTSTRAP] chat_startup_mode is "ask" — entering Ask mode');
      setConversationMode('ask');
      setTimeout(() => {
        setIsSidePanelOpen(false);
        setCurrentArtifactMessages([]);
        if (setLayoutMode) setLayoutMode('full');
      }, 50);
    } else if (conversationMode !== 'workflow') {
      setConversationMode('workflow');
    }
  }, [
    activeChatId,
    askMessages,
    configuredStartupMode,
    consumeNavigationQueryParams,
    conversationBootstrapRef,
    conversationMode,
    currentChatId,
    generalMessagesCacheRef,
    navigationLoading,
    queryChatId,
    queryMode,
    queryResume,
    restoreStoredArtifactForChat,
    resumeOldestFromWidgetRef,
    setConversationMode,
    setCurrentArtifactMessages,
    setIsSidePanelOpen,
    setLayoutMode,
    setMessagesWithLogging,
    urlWorkflowName,
    workflowArtifactSnapshotRef,
  ]);

  useEffect(() => {
    if (conversationMode !== 'ask') return;
    if (connectionStatus !== 'connected') return;
    if (!currentChatId) return;
    if (askModeSyncedChatRef.current === currentChatId) return;

    const activeWs = wsRef.current;
    if (!activeWs || typeof activeWs.send !== 'function') return;

    const sent = activeWs.send({ type: 'chat.enter_general_mode', chat_id: currentChatId });
    if (sent) {
      askModeSyncedChatRef.current = currentChatId;
      console.log('🧭 [ASK_SYNC] chat.enter_general_mode sent for chat:', currentChatId);
    }
  }, [askModeSyncedChatRef, connectionStatus, conversationMode, currentChatId, wsRef]);

  useEffect(() => {
    if (conversationMode === 'ask' && (isSidePanelOpen || layoutMode !== 'full')) {
      console.log('🧹 [ASK_MODE_ENFORCER] Closing artifact panel (Ask mode should not have artifacts)');
      setIsSidePanelOpen(false);
      setCurrentArtifactMessages([]);
      if (setLayoutMode) setLayoutMode('full');
    }
  }, [conversationMode, isSidePanelOpen, layoutMode, setLayoutMode, setCurrentArtifactMessages, setIsSidePanelOpen]);

  useEffect(() => {
    if (conversationMode === 'workflow') {
      const cached = sanitizeVisibleWorkflowMessages(workflowMessagesCacheRef.current);
      if (cached) {
        setMessagesWithLogging(cached);
      }
      return;
    }
    if (conversationMode !== 'ask') {
      return;
    }
    if (generalMessagesCacheRef.current && generalMessagesCacheRef.current.length > 0) {
      setMessagesWithLogging(generalMessagesCacheRef.current);
      return;
    }
    if ((!generalMessagesCacheRef.current || generalMessagesCacheRef.current.length === 0) && messagesRef.current.length > 0) {
      setMessagesWithLogging([]);
    }
    if (!activeGeneralChatId || generalHydrationPendingRef.current) {
      return;
    }
    generalHydrationPendingRef.current = true;
    Promise.resolve(hydrateGeneralTranscript(activeGeneralChatId)).finally(() => {
      generalHydrationPendingRef.current = false;
    });
  }, [
    activeGeneralChatId,
    conversationMode,
    generalHydrationPendingRef,
    generalMessagesCacheRef,
    hydrateGeneralTranscript,
    messagesRef,
    sanitizeVisibleWorkflowMessages,
    setMessagesWithLogging,
    workflowMessagesCacheRef,
  ]);

  useEffect(() => {
    if (conversationMode !== 'workflow') return;
    if (workflowReplayPendingRef.current) return;
    if (messagesRef.current && messagesRef.current.length > 0) return;
    const restoredWorkflowMessages = sanitizeVisibleWorkflowMessages(workflowMessages);
    if (restoredWorkflowMessages.length > 0) {
      console.log(`📦 [WORKFLOW_RESTORE] Restoring ${restoredWorkflowMessages.length} shared workflow messages`);
      setMessagesWithLogging(restoredWorkflowMessages);
    }
  }, [conversationMode, messagesRef, sanitizeVisibleWorkflowMessages, setMessagesWithLogging, workflowMessages, workflowReplayPendingRef]);

  useEffect(() => {
    if (!resumeOldestFromWidgetRef.current) {
      return;
    }
    if (conversationMode !== 'workflow') {
      return;
    }
    resumeOldestFromWidgetRef.current = false;
    handleConversationModeChange('workflow');
  }, [conversationMode, handleConversationModeChange, resumeOldestFromWidgetRef]);

  useEffect(() => {
    if (!queryChatId) {
      return;
    }
    if (queryMode === 'ask') {
      return;
    }
    if (!isPrimaryChatRoute) {
      return;
    }
    if (connectionStatus !== 'connected') {
      return;
    }

    const workflowFromQuery = urlWorkflowName || currentWorkflowName;
    const cacheKey = `${queryChatId}:${workflowFromQuery || ''}`;
    if (queryResumeHandledRef.current === cacheKey) {
      return;
    }

    let cancelled = false;

    const attemptRouteResume = async () => {
      const workflowForCheck =
        resolveKnownWorkflowName(workflowFromQuery)
        || resolveKnownWorkflowName(currentWorkflowName)
        || workflowConfig.getDefaultWorkflow()
        || workflowFromQuery;

      if (api && typeof api.getHttpBaseUrl === 'function' && currentAppId && workflowForCheck) {
        try {
          const response = await fetch(
            `${api.getHttpBaseUrl()}/api/chats/exists/${currentAppId}/${workflowForCheck}/${queryChatId}`
          );
          if (cancelled) {
            return;
          }

          if (response.ok) {
            const result = await response.json();
            if (cancelled) {
              return;
            }
            if (result?.exists === false) {
              console.warn('🧹 [ROUTE_RESUME] Ignoring stale query chat_id after backend reset:', queryChatId);
              clearStoredArtifactState(queryChatId);
              clearStoredChatCacheSeed(queryChatId);
              if (getStoredActiveChatId() === queryChatId) {
                setStoredActiveChatId(null);
              }
              if (activeChatId === queryChatId) {
                setActiveChatId(null);
              }
              queryResumeHandledRef.current = cacheKey;
              consumeNavigationQueryParams(['chat_id']);
              return;
            }
          }
        } catch (err) {
          console.warn('⚠️ [ROUTE_RESUME] Could not validate query chat_id, attempting resume anyway:', err);
        }
      }

      console.log('🧭 [ROUTE_RESUME] Detected chat_id in URL, attempting resume:', { queryChatId, workflowFromQuery });
      if (isInWidgetMode) {
        console.log('🧭 [ROUTE_RESUME] Exiting widget mode for direct resume');
        setIsInWidgetMode(false);
      }

      const success = resumeWorkflowSession(queryChatId, workflowFromQuery);
      if (success) {
        queryResumeHandledRef.current = cacheKey;
        consumeNavigationQueryParams(['chat_id']);
      }
    };

    attemptRouteResume();

    return () => {
      cancelled = true;
    };
  }, [
    activeChatId,
    api,
    connectionStatus,
    consumeNavigationQueryParams,
    currentAppId,
    currentWorkflowName,
    isInWidgetMode,
    isPrimaryChatRoute,
    queryChatId,
    queryMode,
    queryResumeHandledRef,
    resolveKnownWorkflowName,
    resumeWorkflowSession,
    setActiveChatId,
    setIsInWidgetMode,
    urlWorkflowName,
    workflowConfig,
  ]);

  useEffect(() => {
    if (layoutMode !== 'view') {
      return;
    }
    if (!isPrimaryChatRoute || isInWidgetMode) {
      return;
    }
    const hasArtifact = Array.isArray(currentArtifactMessages) && currentArtifactMessages.length > 0;
    const artifactActive = surfaceState?.artifact?.status === 'active';
    if (hasArtifact || artifactActive) {
      return;
    }
    setLayoutMode(layoutModeForConversation);
    if (widgetOverlayOpen) {
      setWidgetOverlayOpen(false);
    }
  }, [
    currentArtifactMessages,
    isInWidgetMode,
    isPrimaryChatRoute,
    layoutMode,
    layoutModeForConversation,
    setLayoutMode,
    setWidgetOverlayOpen,
    surfaceState,
    widgetOverlayOpen,
  ]);
}